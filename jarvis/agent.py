import asyncio
import logging
from typing import Callable, Optional
from google import genai
from google.genai import types

from .audio import AudioManager
from .config import (
    ENABLE_STARTUP_GREETING,
    ACKNOWLEDGMENT_VOICE_EN,
    ACKNOWLEDGMENT_VOICE_HI,
    INSTANT_ACKNOWLEDGMENT_ENABLED,
    MODEL,
    USE_GOOGLE_SEARCH_GROUNDING,
    USER_TITLE,
    VOICE_NAME,
    get_api_key,
    get_masked_api_key,
)
from .acknowledgment import InstantAcknowledgmentEngine
from .echo_guard import SemanticEchoGuard
from .persona import build_system_instruction
from .tools import (
    GET_CURRENCY_EXCHANGE_RATE_DECLARATION,
    GET_STOCK_QUOTE_DECLARATION,
    SCRAPE_WEBPAGE_DECLARATION,
    SEARCH_WEB_DECLARATION,
    handle_tool_call,
)

logger = logging.getLogger("jarvis.agent")


class JarvisAgent:
    """
    Manages the real-time bidirectional WebSocket session with the Gemini Live API.
    Features:
    - Automatic reconnection on network interruption with exponential backoff.
    - Local wake-word gating (transmits audio only when active).
    - Google Search grounding and live web scraping.
    - Real-time barge-in speech interruption.
    - Secret protection (API keys are never logged).
    - 🔇 Self-Echo Guard: Recognizes and drops self-voice echo/tail without muting user.
    """

    def __init__(
        self,
        audio_manager: AudioManager,
        on_user_transcript: Optional[Callable[[str], None]] = None,
        on_model_transcript: Optional[Callable[[str], None]] = None,
        on_search_query: Optional[Callable[[str], None]] = None,
        on_tool_call: Optional[Callable[[str, dict], None]] = None,
        on_interrupted: Optional[Callable[[], None]] = None,
        on_turn_complete: Optional[Callable[[], None]] = None,
        on_status_change: Optional[Callable[[str], None]] = None,
        on_echo_dropped: Optional[Callable[[str], None]] = None,
        on_instant_acknowledgment: Optional[Callable[[str], None]] = None,
    ):
        self.audio_manager = audio_manager
        self.on_user_transcript = on_user_transcript
        self.on_model_transcript = on_model_transcript
        self.on_search_query = on_search_query
        self.on_tool_call = on_tool_call
        self.on_interrupted = on_interrupted
        self.on_turn_complete = on_turn_complete
        self.on_status_change = on_status_change
        self.on_echo_dropped = on_echo_dropped
        self.on_instant_acknowledgment = on_instant_acknowledgment

        # 🔇 Semantic Self-Echo Guard
        self.echo_guard = SemanticEchoGuard(echo_window_seconds=3.0)
        self._suppress_turn = False

        # ⚡ Instant Acknowledgment Engine
        self.acknowledgment_engine = InstantAcknowledgmentEngine(
            voice_en=ACKNOWLEDGMENT_VOICE_EN,
            voice_hi=ACKNOWLEDGMENT_VOICE_HI,
            output_sample_rate=audio_manager.output_rate,
            enabled=INSTANT_ACKNOWLEDGMENT_ENABLED,
        )
        self.acknowledgment_engine.warmup_cache(user_title=USER_TITLE)

        self._last_user_transcript = ""
        self._thinking_ack_task: Optional[asyncio.Task] = None

        self._running = False
        self._api_key = get_api_key()

    def _cancel_thinking_ack(self) -> None:
        """Cancel any pending latency/thinking acknowledgment."""
        if self._thinking_ack_task and not self._thinking_ack_task.done():
            self._thinking_ack_task.cancel()
            self._thinking_ack_task = None

    async def _thinking_watchdog(self) -> None:
        """Triggers a polite acknowledgment if server reasoning takes longer than 2.0s."""
        try:
            await asyncio.sleep(2.0)
            if (
                self._running
                and self.audio_manager.state == AudioManager.STATE_ACTIVE
                and not self.audio_manager.is_speaking
                and self.acknowledgment_engine
                and self.acknowledgment_engine.enabled
            ):
                phrase_text, pcm = self.acknowledgment_engine.get_acknowledgment(
                    tool_name="processing",
                    user_text=self._last_user_transcript,
                    user_title=USER_TITLE,
                )
                if phrase_text:
                    self.echo_guard.record_model_speech(phrase_text)
                    if pcm:
                        self.audio_manager.play_audio(pcm)
                    if self.on_instant_acknowledgment:
                        self.on_instant_acknowledgment(phrase_text)
        except asyncio.CancelledError:
            pass


    def _build_connect_config(self, use_google_search: bool = USE_GOOGLE_SEARCH_GROUNDING) -> types.LiveConnectConfig:
        """Construct the LiveConnectConfig with audio modality, search tools, and persona."""
        tools = [
            types.Tool(
                function_declarations=[
                    SEARCH_WEB_DECLARATION,
                    SCRAPE_WEBPAGE_DECLARATION,
                    GET_STOCK_QUOTE_DECLARATION,
                    GET_CURRENCY_EXCHANGE_RATE_DECLARATION,
                ]
            )
        ]
        if use_google_search:
            tools.append(types.Tool(google_search=types.GoogleSearch()))

        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=VOICE_NAME
                    )
                )
            ),
            system_instruction=types.Content(
                parts=[
                    types.Part.from_text(text=build_system_instruction(USER_TITLE))
                ]
            ),
            tools=tools,
            input_audio_transcription=types.AudioTranscriptionConfig(
                custom_vocabulary=["JARVIS", "Jarvis", "Mark 56", "Tony Stark"]
            ),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

    async def run(self) -> None:
        """
        Main execution loop with automatic reconnection, network error resilience,
        and audio pipeline coordination.
        """
        if not self._api_key:
            raise ValueError(
                "GEMINI_API_KEY is not configured. Set GEMINI_API_KEY in .env "
                "or export it in your environment."
            )

        client = genai.Client(api_key=self._api_key)
        use_google_search = USE_GOOGLE_SEARCH_GROUNDING
        config = self._build_connect_config(use_google_search=use_google_search)

        self._running = True
        self.audio_manager.start()

        retry_delay = 2.0
        max_retry_delay = 30.0
        has_greeted = False

        if self.on_status_change:
            self.on_status_change("INITIALIZING")

        try:
            while self._running:
                try:
                    logger.info(
                        f"Establishing Live API WebSocket with model '{MODEL}' "
                        f"[Key: {get_masked_api_key()}]..."
                    )
                    if self.on_status_change:
                        self.on_status_change("CONNECTING")

                    async with client.aio.live.connect(model=MODEL, config=config) as session:
                        logger.info("Connected to Gemini Live API. J.A.R.V.I.S. is online.")
                        retry_delay = 2.0  # Reset delay on successful connection
                        if self.on_status_change:
                            self.on_status_change("CONNECTED")

                        # Startup greeting if enabled (only once per run)
                        if ENABLE_STARTUP_GREETING and not has_greeted:
                            has_greeted = True
                            greeting_prompt = (
                                f"Jarvis, initialize Mark 56 systems and greet {USER_TITLE} briefly in character."
                            )
                            await session.send_client_content(
                                turns=[
                                    types.Content(
                                        role="user",
                                        parts=[types.Part.from_text(text=greeting_prompt)],
                                    )
                                ],
                                turn_complete=True,
                            )

                        # Create concurrent tasks for send and receive
                        send_task = asyncio.create_task(
                            self._send_mic_audio(session), name="JarvisSendMicAudio"
                        )
                        receive_task = asyncio.create_task(
                            self._receive_server_messages(session), name="JarvisReceiveServer"
                        )

                        done, pending = await asyncio.wait(
                            [send_task, receive_task],
                            return_when=asyncio.FIRST_EXCEPTION,
                        )

                        for task in pending:
                            task.cancel()
                        for task in done:
                            if not task.cancelled() and task.exception():
                                raise task.exception()

                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    if not self._running:
                        break
                    
                    err_str = str(exc).lower()
                    if use_google_search and ("quota" in err_str or "1011" in err_str):
                        logger.warning(
                            "Google Search Grounding quota exceeded. Falling back to built-in live web search..."
                        )
                        use_google_search = False
                        config = self._build_connect_config(use_google_search=False)
                        continue

                    logger.warning(
                        f"Live API session disconnected ({exc}). "
                        f"Reconnecting in {retry_delay:.1f}s..."
                    )
                    if self.on_status_change:
                        self.on_status_change(f"RECONNECTING ({retry_delay:.0f}s)")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 1.5, max_retry_delay)

        finally:
            self._running = False
            self.audio_manager.stop()
            if self.on_status_change:
                self.on_status_change("OFFLINE")

    def stop(self) -> None:
        """Signal agent to terminate loops and shut down cleanly."""
        self._running = False
        self._cancel_thinking_ack()

    async def _send_mic_audio(self, session) -> None:
        """Streams microphone PCM audio chunks to Gemini Live when active."""
        try:
            async for chunk in self.audio_manager.get_audio_input_stream():
                if not self._running:
                    break
                await session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in microphone streaming task: {e}")
            raise

    async def _receive_server_messages(self, session) -> None:
        """Processes incoming audio parts, transcriptions, tools, and interruptions across all turns."""
        try:
            while self._running:
                async for response in session.receive():
                    if not self._running:
                        break

                    # 1. Server content (audio, transcriptions, grounding)
                    content = response.server_content
                    if content:
                        # User interruption signal
                        if content.interrupted:
                            self._cancel_thinking_ack()
                            if not self._suppress_turn:
                                self.audio_manager.interrupt()
                                if self.on_interrupted:
                                    self.on_interrupted()

                        # Speech transcription for JARVIS voice
                        if content.output_transcription and content.output_transcription.text:
                            text = content.output_transcription.text.strip()
                            if text:
                                # Record into Self-Echo Guard
                                self.echo_guard.record_model_speech(text)
                                if not self._suppress_turn:
                                    self.audio_manager.refresh_activity()
                                    if self.on_model_transcript:
                                        self.on_model_transcript(text)

                        # Speech transcription for user voice
                        if content.input_transcription and content.input_transcription.text:
                            text = content.input_transcription.text.strip()
                            if text:
                                # 🔇 Self-Echo Guard: Never answers its own last sentence or voice tail
                                if self.echo_guard.is_self_echo(text):
                                    logger.info(f"Self-Echo Guard recognized echo: '{text}'. Dropping turn.")
                                    self._suppress_turn = True
                                    self._cancel_thinking_ack()
                                    self.audio_manager.interrupt()
                                    if self.on_echo_dropped:
                                        self.on_echo_dropped(text)
                                else:
                                    self._suppress_turn = False
                                    self._last_user_transcript = text
                                    self.audio_manager.refresh_activity()
                                    if self.on_user_transcript:
                                        self.on_user_transcript(text)
                                    # Schedule latency watchdog if server takes long to think
                                    self._cancel_thinking_ack()
                                    self._thinking_ack_task = asyncio.create_task(self._thinking_watchdog())

                        # Spoken audio parts
                        if content.model_turn and content.model_turn.parts:
                            self._cancel_thinking_ack()
                            if not self._suppress_turn:
                                for part in content.model_turn.parts:
                                    if part.inline_data and part.inline_data.data:
                                        self.audio_manager.play_chunk(part.inline_data.data)

                        # Grounding search queries
                        if content.grounding_metadata and hasattr(content.grounding_metadata, "web_search_queries"):
                            if not self._suppress_turn:
                                queries = content.grounding_metadata.web_search_queries
                                if queries and self.on_search_query:
                                    for q in queries:
                                        self.on_search_query(q)

                        # Turn completed
                        if content.turn_complete:
                            self._cancel_thinking_ack()
                            if self._suppress_turn:
                                self._suppress_turn = False
                            elif self.on_turn_complete:
                                self.on_turn_complete()

                    # 2. Tool calls (e.g. web search / scraping)
                    if response.tool_call and response.tool_call.function_calls:
                        self._cancel_thinking_ack()
                        # ⚡ Instant Acknowledgment: Speaks a short, context-aware reply in user's language immediately
                        if self.acknowledgment_engine and self.acknowledgment_engine.enabled:
                            for fc in response.tool_call.function_calls:
                                phrase_text, pcm = self.acknowledgment_engine.get_acknowledgment(
                                    tool_name=fc.name,
                                    user_text=self._last_user_transcript,
                                    user_title=USER_TITLE,
                                )
                                if phrase_text:
                                    # Record in SemanticEchoGuard so it is recognized as self-voice
                                    self.echo_guard.record_model_speech(phrase_text)
                                    if pcm:
                                        self.audio_manager.play_audio(pcm)
                                    if self.on_instant_acknowledgment:
                                        self.on_instant_acknowledgment(phrase_text)
                                break  # Acknowledge primary tool call

                        function_responses = []
                        for fc in response.tool_call.function_calls:
                            if self.on_tool_call:
                                self.on_tool_call(fc.name, fc.args or {})

                            loop = asyncio.get_running_loop()
                            result = await loop.run_in_executor(
                                None, handle_tool_call, fc.name, fc.args or {}
                            )

                            function_responses.append(
                                types.FunctionResponse(
                                    id=fc.id,
                                    name=fc.name,
                                    response=result,
                                )
                            )

                        logger.debug(f"Sending tool response: {function_responses}")
                        await session.send_tool_response(function_responses=function_responses)

                    # 3. Server GoAway notification
                    if response.go_away:
                        logger.warning(f"Server sent GoAway signal: {response.go_away}")
                        return
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in server message receiver: {e}")
            raise
