import logging
import re
import time
from collections import deque
from typing import Deque, List, Optional, Tuple
import numpy as np

logger = logging.getLogger("jarvis.echo_guard")


class AcousticEchoGuard:
    """
    Real-time Acoustic Echo Guard for the audio pipeline.
    - Tracks audio playing through speakers and its reverberation decay tail (~350ms).
    - Recognizes when microphone audio contains the echo or tail of JARVIS's own voice
      and drops it, preventing self-feedback.
    - Detects user barge-in so the user is NEVER muted when they choose to speak.
    """

    def __init__(
        self,
        tail_decay_seconds: float = 0.35,
        correlation_threshold: float = 0.38,
        barge_in_rms_threshold: float = 2200.0,
    ):
        self.tail_decay_seconds = tail_decay_seconds
        self.correlation_threshold = correlation_threshold
        self.barge_in_rms_threshold = barge_in_rms_threshold

        self.last_playback_time = 0.0
        # Reference buffer of 16kHz audio: 2.0 seconds = 32000 samples
        self._ref_buffer = np.zeros(32000, dtype=np.float32)

    def feed_speaker_audio(self, chunk_24k_bytes: bytes) -> None:
        """
        Record audio playing through speakers into the reference buffer,
        resampled to 16kHz for direct microphone correlation.
        """
        if not chunk_24k_bytes:
            return
        self.last_playback_time = time.time()
        try:
            arr_24k = np.frombuffer(chunk_24k_bytes, dtype=np.int16).astype(np.float32)
            if len(arr_24k) == 0:
                return

            # Resample 24k -> 16k using fast linear interpolation
            target_len = int(len(arr_24k) * 16 / 24)
            indices = np.linspace(0, len(arr_24k) - 1, target_len)
            arr_16k = np.interp(indices, np.arange(len(arr_24k)), arr_24k)

            n = len(arr_16k)
            if n >= len(self._ref_buffer):
                self._ref_buffer[:] = arr_16k[-len(self._ref_buffer):]
            else:
                self._ref_buffer[:-n] = self._ref_buffer[n:]
                self._ref_buffer[-n:] = arr_16k
        except Exception as e:
            logger.debug(f"Error buffering speaker audio: {e}")

    def inspect_frame(self, mic_chunk_bytes: bytes, is_speaking: bool) -> Tuple[bool, bool]:
        """
        Analyze a 16kHz microphone chunk.
        Returns:
            (is_echo, is_barge_in)
            - is_echo = True: The frame is recognized as JARVIS's voice or echo tail -> drop it.
            - is_barge_in = True: The user is actively speaking over JARVIS -> allow and interrupt.
        """
        now = time.time()
        time_since_speech = now - self.last_playback_time

        # Normal idle condition: JARVIS is not speaking and echo tail has passed
        if not is_speaking and time_since_speech > self.tail_decay_seconds:
            return False, False

        mic_arr = np.frombuffer(mic_chunk_bytes, dtype=np.int16).astype(np.float32)
        if len(mic_arr) == 0:
            return False, False

        mic_rms = float(np.sqrt(np.mean(np.square(mic_arr))))
        if mic_rms < 120.0:
            # Ambient silence during speech or tail decay -> drop immediately without running correlation
            return True, False

        mic_norm = float(np.linalg.norm(mic_arr))
        if mic_norm < 1e-4:
            return True, False

        # Check correlation against the last ~200ms of speaker reference
        search_window = self._ref_buffer[-3200:]
        ref_norm = float(np.linalg.norm(search_window))
        if ref_norm < 100.0:
            # Speaker reference is effectively silent
            return False, False

        corr = np.correlate(search_window, mic_arr, mode="valid")
        max_corr = float(np.max(corr))
        norm_corr = max_corr / (mic_norm * float(np.linalg.norm(search_window[: len(mic_arr)])) + 1e-6)

        # 1. High correlation with speaker reference -> pure echo of JARVIS voice
        if norm_corr >= self.correlation_threshold:
            # Echo confirmed. If user speaks loudly over it, it may be barge-in:
            if mic_rms > self.barge_in_rms_threshold and norm_corr < 0.75:
                logger.debug(f"Barge-in detected during playback (RMS: {mic_rms:.0f}, corr: {norm_corr:.2f})")
                return False, True
            return True, False

        # 2. In tail decay window (playback just stopped within ~350ms):
        if not is_speaking and time_since_speech <= self.tail_decay_seconds:
            # If mic energy is low/moderate, it is room reverberation of the voice tail
            if mic_rms < self.barge_in_rms_threshold:
                return True, False
            # User started speaking during tail window
            return False, True

        # 3. Active playback without high correlation:
        if is_speaking:
            if mic_rms > self.barge_in_rms_threshold:
                logger.info(f"User barge-in detected (RMS: {mic_rms:.0f}).")
                return False, True
            # Speaker bleed with distortion -> drop to protect assistant from self-trigger
            return True, False

        return False, False


class SemanticEchoGuard:
    """
    Transcript-level Self-Echo Guard.
    Never allows JARVIS to answer its own last sentence or the tail of its voice.
    Recognizes echoed sentences, trailing phrases, and phoneme artifacts, and drops them.
    """

    def __init__(self, echo_window_seconds: float = 3.0):
        self.echo_window_seconds = echo_window_seconds
        self._recent_sentences: Deque[Tuple[str, float]] = deque(maxlen=8)
        self.last_speech_time = 0.0

    def record_model_speech(self, text: str) -> None:
        """Record model sentence when output transcription or spoken turn is received."""
        if not text or not text.strip():
            return
        self.last_speech_time = time.time()
        norm = self._normalize(text)
        if norm:
            self._recent_sentences.append((norm, self.last_speech_time))

    def is_self_echo(self, text: str) -> bool:
        """
        Check if user input transcript is an echo of the model's own voice or tail.
        """
        if not text or not text.strip():
            return False

        now = time.time()
        time_since_speech = now - self.last_speech_time
        if time_since_speech > self.echo_window_seconds:
            return False

        norm_input = self._normalize(text)
        if not norm_input:
            return False

        input_words = norm_input.split()

        for sent, sent_time in reversed(self._recent_sentences):
            if now - sent_time > self.echo_window_seconds:
                continue

            sent_words = sent.split()

            # 1. Exact sentence or substring match
            if norm_input == sent or norm_input in sent:
                return True

            # 2. Tail / suffix match (e.g. last 1 to 6 words of what model said)
            for n in range(1, min(len(sent_words) + 1, 7)):
                tail = " ".join(sent_words[-n:])
                if norm_input == tail:
                    return True

            # 3. Trailing keywords / address titles ("sir", "divyanshu", etc.)
            if len(input_words) <= 2 and norm_input in sent:
                return True

            # 4. High overlap ratio (>= 70% of words were in the model's last sentence)
            overlap = set(input_words).intersection(set(sent_words))
            if len(input_words) > 0 and len(overlap) / len(input_words) >= 0.70:
                return True

            # 5. Phoneme / tiny scrap match (1-2 chars) arriving within 1.5s of speech
            if len(norm_input) <= 2 and time_since_speech < 1.5:
                return True

        return False

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.lower()
        text = re.sub(r"[^\w\s]", "", text)
        return " ".join(text.split())
