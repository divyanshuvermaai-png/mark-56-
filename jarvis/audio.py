import asyncio
import collections
from contextlib import contextmanager
import logging
import os
import queue
import sys
import threading
import time
from typing import AsyncGenerator, Callable, Deque, List, Optional
import numpy as np
import pyaudio

from .config import (
    AUDIO_CHANNELS,
    AUDIO_INPUT_DEVICE,
    AUDIO_OUTPUT_DEVICE,
    CHUNK_SIZE,
    CONVERSATION_TIMEOUT,
    INPUT_SAMPLE_RATE,
    MUTE_MIC_DURING_SPEECH,
    OUTPUT_SAMPLE_RATE,
    WAKE_WORD_ENABLED,
    WAKE_WORD_THRESHOLD,
)
from .echo_guard import AcousticEchoGuard
from .wakeword import WakeWordDetector

logger = logging.getLogger("jarvis.audio")


class AudioDeviceError(Exception):
    """Raised when an audio input or output device cannot be initialized."""
    pass


@contextmanager
def suppress_c_stderr():
    """
    Suppress low-level C library stderr outputs (e.g. ALSA / PortAudio / Jack debug noise on Linux).
    Prevents terminal pollution across diverse Linux distributions and soundcards.
    """
    try:
        null_fd = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        os.dup2(null_fd, 2)
        os.close(null_fd)
        try:
            yield
        finally:
            os.dup2(old_stderr, 2)
            os.close(old_stderr)
    except Exception:
        yield


def list_audio_devices() -> List[dict]:
    """Inspect and return available audio input and output devices."""
    devices = []
    p = None
    try:
        with suppress_c_stderr():
            p = pyaudio.PyAudio()
        for i in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(i)
                devices.append({
                    "index": i,
                    "name": info.get("name", "Unknown"),
                    "max_input_channels": info.get("maxInputChannels", 0),
                    "max_output_channels": info.get("maxOutputChannels", 0),
                    "default_sample_rate": info.get("defaultSampleRate", 0),
                })
            except Exception:
                pass
    except Exception as exc:
        logger.warning(f"Failed to query audio devices: {exc}")
    finally:
        if p:
            try:
                with suppress_c_stderr():
                    p.terminate()
            except Exception:
                pass
    return devices


def resolve_device_index(p: pyaudio.PyAudio, device_spec: str, is_input: bool) -> Optional[int]:
    """
    Resolve an audio device specification (index or name substring) to a PyAudio device index.
    Returns None if default system device should be used.
    """
    if not device_spec:
        return None

    # Check for direct integer index
    try:
        idx = int(device_spec)
        info = p.get_device_info_by_index(idx)
        channels = info.get("maxInputChannels" if is_input else "maxOutputChannels", 0)
        if channels > 0:
            return idx
    except (ValueError, IOError):
        pass

    # Search by name substring
    target = device_spec.lower()
    for i in range(p.get_device_count()):
        try:
            info = p.get_device_info_by_index(i)
            channels = info.get("maxInputChannels" if is_input else "maxOutputChannels", 0)
            name = info.get("name", "").lower()
            if channels > 0 and target in name:
                logger.info(f"Resolved device '{device_spec}' to index {i} ('{info.get('name')}')")
                return i
        except Exception:
            continue

    logger.warning(
        f"Audio device matching '{device_spec}' (is_input={is_input}) was not found. "
        "Falling back to default device."
    )
    return None


class AudioManager:
    """
    Audio subsystem for JARVIS Mark 56.
    - Captures 16kHz PCM mono from microphone with universal hardware rate negotiation (auto-resampling).
    - Plays 24kHz PCM mono to speakers/AUX output with auto-resampling to native DAC rates.
    - Implements local wake-word gating (zero audio transmission while idle).
    - Supports real-time interruption and hardware failure handling.
    - Cross-platform: Works on macOS, Linux (ALSA/PulseAudio/PipeWire), and Windows.
    """

    STATE_IDLE = "IDLE"
    STATE_ACTIVE = "ACTIVE"

    def __init__(
        self,
        input_rate: int = INPUT_SAMPLE_RATE,
        output_rate: int = OUTPUT_SAMPLE_RATE,
        chunk_size: int = CHUNK_SIZE,
        channels: int = AUDIO_CHANNELS,
        input_device_spec: str = AUDIO_INPUT_DEVICE,
        output_device_spec: str = AUDIO_OUTPUT_DEVICE,
        mute_mic_during_speech: bool = MUTE_MIC_DURING_SPEECH,
        wake_word_enabled: bool = WAKE_WORD_ENABLED,
        wake_word_threshold: float = WAKE_WORD_THRESHOLD,
        conversation_timeout: float = CONVERSATION_TIMEOUT,
        on_wake_detected: Optional[Callable[[], None]] = None,
        on_state_change: Optional[Callable[[str], None]] = None,
    ):
        self.input_rate = input_rate
        self.output_rate = output_rate
        self.chunk_size = chunk_size
        self.channels = channels
        self.input_device_spec = input_device_spec
        self.output_device_spec = output_device_spec
        self.mute_mic_during_speech = mute_mic_during_speech
        self.wake_word_enabled = wake_word_enabled
        self.conversation_timeout = conversation_timeout

        self.on_wake_detected = on_wake_detected
        self.on_state_change = on_state_change

        # Universal hardware sample rate tracking
        self._actual_input_rate = self.input_rate
        self._actual_output_rate = self.output_rate
        self._input_frames_per_buffer = self.chunk_size

        # Local wake-word detector
        self.wake_detector = WakeWordDetector(threshold=wake_word_threshold)
        if not self.wake_detector.enabled and self.wake_word_enabled:
            logger.warning(
                "Wake word model is not operational. Reverting to continuous active mode."
            )
            self.wake_word_enabled = False

        # State management: IDLE = local listening only, ACTIVE = transmitting to API
        self.state = self.STATE_IDLE if self.wake_word_enabled else self.STATE_ACTIVE
        self._last_active_time = time.time() if self.state == self.STATE_ACTIVE else 0.0

        self._pyaudio: Optional[pyaudio.PyAudio] = None
        self._input_stream: Optional[pyaudio.Stream] = None
        self._output_stream: Optional[pyaudio.Stream] = None

        self._input_queue: queue.Queue[bytes] = queue.Queue(maxsize=120)
        self._output_queue: queue.Queue[bytes] = queue.Queue(maxsize=250)
        self._preroll_buffer: Deque[bytes] = collections.deque(maxlen=30)  # ~1.0s pre-roll buffer

        self._running = False
        self._input_thread: Optional[threading.Thread] = None
        self._output_thread: Optional[threading.Thread] = None

        self._is_speaking = False
        self._last_speech_time = 0.0

        # 🔇 Acoustic Self-Echo Guard (drops voice tail & echo without muting user)
        self.echo_guard = AcousticEchoGuard()

    @staticmethod
    def _resample_pcm(data_bytes: bytes, from_rate: int, to_rate: int) -> bytes:
        """
        Fast, robust PCM resampler using NumPy.
        Optimized for integer ratios (3:1, 2:1) and uses linear interpolation for arbitrary rates.
        """
        if from_rate == to_rate or not data_bytes:
            return data_bytes
        try:
            arr = np.frombuffer(data_bytes, dtype=np.int16)
            if len(arr) == 0:
                return data_bytes
            # Integer ratio optimizations
            if from_rate == 48000 and to_rate == 16000:
                return arr[::3].tobytes()
            elif from_rate == 24000 and to_rate == 48000:
                return np.repeat(arr, 2).tobytes()
            elif from_rate == 16000 and to_rate == 48000:
                return np.repeat(arr, 3).tobytes()
            elif from_rate == 32000 and to_rate == 16000:
                return arr[::2].tobytes()
            elif from_rate == 16000 and to_rate == 32000:
                return np.repeat(arr, 2).tobytes()
            else:
                # Linear interpolation for non-integer rates (e.g. 44100 <-> 16000 / 24000)
                target_len = int(len(arr) * to_rate / from_rate)
                indices = np.linspace(0, len(arr) - 1, target_len)
                resampled = np.interp(indices, np.arange(len(arr)), arr).astype(np.int16)
                return resampled.tobytes()
        except Exception as e:
            logger.debug(f"Resampling error: {e}")
            return data_bytes

    @property
    def is_speaking(self) -> bool:
        """Returns True if audio is actively playing or queued to play."""
        return self._is_speaking or (time.time() - self._last_speech_time < 0.25)

    def set_active(self) -> None:
        """Transition to ACTIVE state: transmit microphone audio to Live API."""
        if self.state != self.STATE_ACTIVE:
            self.state = self.STATE_ACTIVE
            self._last_active_time = time.time()
            if self.on_state_change:
                self.on_state_change(self.STATE_ACTIVE)

    def set_idle(self) -> None:
        """Transition to IDLE state: gate microphone audio locally for wake-word only."""
        if self.state != self.STATE_IDLE and self.wake_word_enabled:
            self.state = self.STATE_IDLE
            self.wake_detector.reset()
            # Flush any pending unread audio frames
            while not self._input_queue.empty():
                try:
                    self._input_queue.get_nowait()
                    self._input_queue.task_done()
                except queue.Empty:
                    break
            if self.on_state_change:
                self.on_state_change(self.STATE_IDLE)

    def refresh_activity(self) -> None:
        """Reset the conversation activity timer while active."""
        if self.state == self.STATE_ACTIVE:
            self._last_active_time = time.time()

    def start(self) -> None:
        """Initialize audio hardware streams and start capture/playback worker threads."""
        if self._running:
            return

        try:
            with suppress_c_stderr():
                self._pyaudio = pyaudio.PyAudio()
        except Exception as exc:
            raise AudioDeviceError(f"Failed to initialize PyAudio driver: {exc}")

        # Resolve hardware device indices
        in_idx = resolve_device_index(self._pyaudio, self.input_device_spec, is_input=True)
        out_idx = resolve_device_index(self._pyaudio, self.output_device_spec, is_input=False)

        # Open Microphone Input Stream (Auto-probe supported rates: 16k -> 48k -> 44.1k -> 32k)
        candidate_in_rates = [self.input_rate, 48000, 44100, 32000, 22050]
        self._input_stream = None
        self._actual_input_rate = self.input_rate
        self._input_frames_per_buffer = self.chunk_size

        for in_rate in candidate_in_rates:
            try:
                frames_per_buf = int(self.chunk_size * in_rate / self.input_rate)
                stream_kwargs = {
                    "format": pyaudio.paInt16,
                    "channels": self.channels,
                    "rate": in_rate,
                    "input": True,
                    "frames_per_buffer": frames_per_buf,
                }
                if in_idx is not None:
                    stream_kwargs["input_device_index"] = in_idx

                with suppress_c_stderr():
                    self._input_stream = self._pyaudio.open(**stream_kwargs)
                self._actual_input_rate = in_rate
                self._input_frames_per_buffer = frames_per_buf
                if in_rate != self.input_rate:
                    logger.info(
                        f"Microphone opened with native hardware rate {in_rate}Hz (auto-resampling to {self.input_rate}Hz)."
                    )
                break
            except Exception as exc:
                logger.debug(f"Microphone probe at {in_rate}Hz failed: {exc}")

        if not self._input_stream:
            self.stop()
            raise AudioDeviceError(
                f"Failed to open audio input stream (Microphone). Device: '{self.input_device_spec or 'Default'}'."
            )

        # Open Speaker / AUX Output Stream (Auto-probe supported rates: 24k -> 48k -> 44.1k -> 32k)
        candidate_out_rates = [self.output_rate, 48000, 44100, 32000, 16000]
        self._output_stream = None
        self._actual_output_rate = self.output_rate

        for out_rate in candidate_out_rates:
            try:
                frames_per_buf = int(self.chunk_size * 2 * out_rate / self.output_rate)
                out_kwargs = {
                    "format": pyaudio.paInt16,
                    "channels": self.channels,
                    "rate": out_rate,
                    "output": True,
                    "frames_per_buffer": frames_per_buf,
                }
                if out_idx is not None:
                    out_kwargs["output_device_index"] = out_idx

                with suppress_c_stderr():
                    self._output_stream = self._pyaudio.open(**out_kwargs)
                self._actual_output_rate = out_rate
                if out_rate != self.output_rate:
                    logger.info(
                        f"Speaker opened with native hardware rate {out_rate}Hz (auto-resampling from {self.output_rate}Hz)."
                    )
                break
            except Exception as exc:
                logger.debug(f"Speaker probe at {out_rate}Hz failed: {exc}")

        if not self._output_stream:
            self.stop()
            raise AudioDeviceError(
                f"Failed to open audio output stream (Speaker/AUX). Device: '{self.output_device_spec or 'Default'}'."
            )

        self._running = True

        self._input_thread = threading.Thread(
            target=self._capture_worker, name="JarvisMicCapture", daemon=True
        )
        self._output_thread = threading.Thread(
            target=self._playback_worker, name="JarvisSpeakerPlayback", daemon=True
        )

        self._input_thread.start()
        self._output_thread.start()
        logger.info(
            f"Audio engine online (State: {self.state}, Mic {self._actual_input_rate}Hz -> Speaker {self._actual_output_rate}Hz)."
        )

    def _capture_worker(self) -> None:
        """Continuously reads PCM audio frames from the microphone."""
        while self._running and self._input_stream:
            try:
                if self._input_stream.is_stopped():
                    try:
                        self._input_stream.start_stream()
                    except Exception:
                        pass
                raw_data = self._input_stream.read(self._input_frames_per_buffer, exception_on_overflow=False)
                if not raw_data:
                    continue

                # Auto-resample to 16000 Hz if hardware opened at a different native rate
                if self._actual_input_rate != self.input_rate:
                    data = self._resample_pcm(raw_data, self._actual_input_rate, self.input_rate)
                else:
                    data = raw_data

                # 🔇 Self-Echo Guard: inspect chunk for echo and tail of JARVIS's voice
                is_echo, is_barge_in = self.echo_guard.inspect_frame(data, self.is_speaking)
                if is_barge_in:
                    # User is actively speaking over JARVIS! Interrupt playback and let audio through
                    self.interrupt()
                elif is_echo:
                    # Echo or voice tail recognized -> drop without muting user
                    continue

                # Echo suppression override: if hard-mute is explicitly enabled, ignore remaining mic chunks
                if self.mute_mic_during_speech and self.is_speaking:
                    continue

                # Gated by Wake Word state:
                if self.state == self.STATE_IDLE and self.wake_word_enabled:
                    # LOCAL DETECTION ONLY: Do NOT transmit audio over the network
                    self._preroll_buffer.append(data)
                    if self.wake_detector.process_frame(data):
                        self.set_active()
                        if self.on_wake_detected:
                            self.on_wake_detected()
                        # Flush pre-roll buffer into network queue
                        while self._preroll_buffer:
                            try:
                                self._input_queue.put_nowait(self._preroll_buffer.popleft())
                            except queue.Full:
                                break
                    continue

                # Active conversation state:
                if self.state == self.STATE_ACTIVE:
                    # Check for conversation timeout (return to idle if silent)
                    if (
                        self.wake_word_enabled
                        and not self.is_speaking
                        and (time.time() - self._last_active_time > self.conversation_timeout)
                    ):
                        logger.info("Conversation timed out. Returning to idle state.")
                        self.set_idle()
                        continue

                    if not self._input_queue.full():
                        self._input_queue.put_nowait(data)

            except Exception as e:
                if self._running:
                    logger.debug(f"Input stream read error: {e}")
                time.sleep(0.01)

    def _playback_worker(self) -> None:
        """Continuously consumes audio frames and plays them to the speaker."""
        while self._running:
            try:
                chunk = self._output_queue.get(timeout=0.04)
                if chunk and self._output_stream:
                    self._is_speaking = True
                    self._last_speech_time = time.time()
                    self.refresh_activity()
                    try:
                        # Auto-resample to hardware output rate if different from 24000 Hz
                        if self._actual_output_rate != self.output_rate:
                            write_chunk = self._resample_pcm(chunk, self.output_rate, self._actual_output_rate)
                        else:
                            write_chunk = chunk
                        if self._output_stream.is_stopped():
                            try:
                                self._output_stream.start_stream()
                            except Exception:
                                pass
                        self._output_stream.write(write_chunk, exception_on_underflow=False)
                    except Exception as e:
                        logger.debug(f"Output stream write error: {e}")
                self._output_queue.task_done()
            except queue.Empty:
                self._is_speaking = False
            except Exception as e:
                logger.debug(f"Playback worker error: {e}")

    def play_chunk(self, audio_data: bytes) -> None:
        """Queue raw PCM 24kHz audio bytes for immediate playback."""
        if not self._running:
            return
        # Feed into AcousticEchoGuard
        self.echo_guard.feed_speaker_audio(audio_data)
        try:
            self._output_queue.put_nowait(audio_data)
            self._is_speaking = True
            self._last_speech_time = time.time()
            self.refresh_activity()
        except queue.Full:
            try:
                self._output_queue.get_nowait()
                self._output_queue.put_nowait(audio_data)
            except Exception:
                pass

    def play_audio(self, pcm_bytes: bytes, chunk_size: int = 4800) -> None:
        """
        Play a complete PCM 24kHz buffer by streaming it in chunks.
        Allows progressive echo guard tracking and instantaneous interruption response.
        """
        if not pcm_bytes or not self._running:
            return
        for i in range(0, len(pcm_bytes), chunk_size):
            if not self._running:
                break
            self.play_chunk(pcm_bytes[i : i + chunk_size])

    def interrupt(self) -> None:
        """
        Immediately cancel and flush all buffered speech when the user speaks.
        Discards pending output frames instantly.
        """
        flushed_count = 0
        while not self._output_queue.empty():
            try:
                self._output_queue.get_nowait()
                self._output_queue.task_done()
                flushed_count += 1
            except queue.Empty:
                break

        self._is_speaking = False
        self._last_speech_time = 0.0

        if self._output_stream and self._pyaudio:
            try:
                self._output_stream.stop_stream()
                self._output_stream.start_stream()
            except Exception as e:
                logger.debug(f"Error resetting output stream on interrupt: {e}")

        logger.debug(f"Playback interrupted. Flushed {flushed_count} queued audio chunks.")

    async def get_audio_input_stream(self) -> AsyncGenerator[bytes, None]:
        """
        Async generator that yields raw PCM audio bytes (16kHz)
        captured from the microphone ONLY when in ACTIVE state.
        """
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                data = await loop.run_in_executor(None, self._read_next_input_chunk)
                if data:
                    yield data
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Error reading audio queue: {e}")
                await asyncio.sleep(0.01)

    def _read_next_input_chunk(self) -> Optional[bytes]:
        try:
            return self._input_queue.get(timeout=0.05)
        except queue.Empty:
            return None

    def stop(self) -> None:
        """Stop threads and release all audio hardware streams."""
        self._running = False

        if self._input_thread and self._input_thread.is_alive():
            self._input_thread.join(timeout=0.5)

        if self._output_thread and self._output_thread.is_alive():
            self._output_thread.join(timeout=0.5)

        if self._input_stream:
            try:
                self._input_stream.stop_stream()
                self._input_stream.close()
            except Exception:
                pass
            self._input_stream = None

        if self._output_stream:
            try:
                self._output_stream.stop_stream()
                self._output_stream.close()
            except Exception:
                pass
            self._output_stream = None

        if self._pyaudio:
            try:
                with suppress_c_stderr():
                    self._pyaudio.terminate()
            except Exception:
                pass
            self._pyaudio = None

        logger.info("Audio engine stopped.")
