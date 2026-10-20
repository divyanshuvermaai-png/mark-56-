#!/usr/bin/env python3
"""
J.A.R.V.I.S. Mark 56 - Gemini Live API Voice Assistant
Headless, voice-only assistant with local wake word and live web grounding.
"""

import os
import sys
from pathlib import Path

# Auto re-exec inside local virtualenv if invoked with system python3
_venv_dir = Path(__file__).resolve().parent / ".venv"
_venv_python = _venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
if sys.prefix == sys.base_prefix and _venv_python.is_file() and sys.executable != str(_venv_python):
    os.execv(str(_venv_python), [str(_venv_python)] + sys.argv)

import asyncio
import signal
import logging
from datetime import datetime

from jarvis.config import (
    AUDIO_INPUT_DEVICE,
    AUDIO_OUTPUT_DEVICE,
    CONVERSATION_TIMEOUT,
    MODEL,
    MUTE_MIC_DURING_SPEECH,
    USE_GOOGLE_SEARCH_GROUNDING,
    USER_TITLE,
    VOICE_NAME,
    WAKE_WORD_ENABLED,
    WAKE_WORD_THRESHOLD,
    get_api_key,
    get_masked_api_key,
    get_masked_alpha_vantage_key,
)
from jarvis.audio import AudioManager, list_audio_devices, AudioDeviceError
from jarvis.agent import JarvisAgent

# ANSI Color Codes
CYAN = "\033[96m"
BLUE = "\033[94m"
GOLD = "\033[93m"
GREEN = "\033[92m"
GRAY = "\033[90m"
RED = "\033[91m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
RESET = "\033[0m"


def print_banner():
    grounding_desc = "Live Web Search + Web Scraper" if not USE_GOOGLE_SEARCH_GROUNDING else "Google Search + Web Scraper"
    banner = f"""
{CYAN}{BOLD}================================================================================
       ___     _     ____   __     __  ___   ____     __  __             _       ____    __   
      |_ _|   / \\   |  _ \\  \\ \\   / / |_ _| / ___|   |  \\/  | __ _ _ __ | | __  | ___|  / /_  
       | |   / _ \\  | |_) |  \\ \\ / /   | |  \\___ \\   | |\\/| |/ _` | '__|| |/ /  |___ \\ | '_ \\ 
    | |_| |  / ___ \\ |  _ <    \\ V /    | |   ___) |  | |  | | (_| | |   |   <    ___) || (_) |
     \\___/  /_/   \\_\\|_| \\_\\    \\_/    |___| |____/   |_|  |_|\\__,_|_|   |_|\\_\\  |____/  \\___/ 
================================================================================{RESET}
{BOLD}SYSTEM:{RESET}          J.A.R.V.I.S. Protocol (Mark 56 Prototype)
{BOLD}ARCHITECTURE:{RESET}    Gemini Live API (Bidirectional WebSocket)
{BOLD}ENGINE:{RESET}          {MODEL}
{BOLD}VOCAL PROFILE:{RESET}   {VOICE_NAME} (British Articulate)
{BOLD}API CREDENTIALS:{RESET} {get_masked_api_key()}
{BOLD}GROUNDING:{RESET}       {grounding_desc}
{BOLD}FINANCIAL DATA:{RESET}  Alpha Vantage ({get_masked_alpha_vantage_key()})
{BOLD}LOCAL WAKE WORD:{RESET} {'Enabled ("Jarvis" / "Hey Jarvis")' if WAKE_WORD_ENABLED else 'Disabled (Continuous streaming)'}
{BOLD}SELF-ECHO GUARD:{RESET} {GREEN}Active (Dual Acoustic + Semantic Tail Guard){RESET}
{BOLD}INSTANT ACK:{RESET}      {GREEN}Active (Zero-Wait Context-Aware Vocal Reply){RESET}
{BOLD}AUDIO IN / OUT:{RESET}  {AUDIO_INPUT_DEVICE or 'Default Mic'} -> {AUDIO_OUTPUT_DEVICE or 'Default Speaker/AUX'}
{BOLD}INTERFACE:{RESET}       Voice-Only / Headless (No UI)
{BOLD}STATUS:{RESET}          {GREEN}Online & Operational{RESET}
--------------------------------------------------------------------------------
{GRAY}* Wake word detection runs strictly LOCALLY on CPU while idle.
* Zero audio is transmitted over the network until activated.
* Equipped with real-time web search for news, facts, and live information.
* Real-time stock prices & currency conversion powered by Alpha Vantage.
* ⚡ Instant Acknowledgment speaks in your language the instant tasks start.
* Self-Echo Guard drops voice tails & self-echoes without muting you.
* Headset or AUX-connected speaker supported.
* Press [Ctrl+C] to shut down.{RESET}
================================================================================
"""
    print(banner)


class ConsoleReporter:
    def __init__(self):
        self._last_speaker = None

    def on_user_transcript(self, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\n{BLUE}{BOLD}[{timestamp}] [{USER_TITLE}]:{RESET} {text}")
        self._last_speaker = "user"

    def on_model_transcript(self, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        if self._last_speaker != "model":
            print(f"{GOLD}{BOLD}[{timestamp}] [J.A.R.V.I.S.]:{RESET} ", end="", flush=True)
            self._last_speaker = "model"
        print(f"{GOLD}{text}{RESET} ", end="", flush=True)

    def on_search_query(self, query: str):
        print(f"\n{GRAY}[Live Search]: Querying Google for '{query}'...{RESET}")

    def on_tool_call(self, name: str, args: dict):
        if name == "search_web":
            query = args.get("query", "")
            print(f"\n{GRAY}[Live Search]: Querying web for '{query}'...{RESET}")
        elif name == "scrape_webpage":
            url = args.get("url", "")
            print(f"\n{GRAY}[Web Scraper]: Extracting content from {url}...{RESET}")
        elif name == "get_stock_quote":
            symbol = args.get("symbol", "")
            print(f"\n{GRAY}[Stock Market]: Querying Alpha Vantage for '{symbol}' quote...{RESET}")
        elif name == "get_currency_exchange_rate":
            from_curr = args.get("from_currency", "")
            to_curr = args.get("to_currency", "")
            print(f"\n{GRAY}[Forex/Crypto]: Querying Alpha Vantage for {from_curr}/{to_curr} conversion...{RESET}")
        else:
            print(f"\n{GRAY}[Tool Call]: Executing {name}...{RESET}")

    def on_interrupted(self):
        print(f"\n{RED}[Speech Interrupted]{RESET}")
        self._last_speaker = None

    def on_turn_complete(self):
        if self._last_speaker == "model":
            print()  # Finalize model turn line
            self._last_speaker = None

    def on_wake_detected(self):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\n{MAGENTA}{BOLD}[{timestamp}] [Wake Word]: 'Jarvis' activated. Voice channel opened.{RESET}")

    def on_echo_dropped(self, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\n{GRAY}[{timestamp}] [Self-Echo Guard]: Dropped echo of model voice ('{text}').{RESET}")

    def on_instant_acknowledgment(self, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\n{GOLD}{BOLD}[{timestamp}] [Instant Acknowledgment]:{RESET} {GOLD}{text}{RESET}")

    def on_audio_state_change(self, state: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        if state == AudioManager.STATE_IDLE:
            print(f"{GRAY}[{timestamp}] [Audio]: Channel idle. Listening locally for wake word...{RESET}")
        elif state == AudioManager.STATE_ACTIVE:
            print(f"{GRAY}[{timestamp}] [Audio]: Channel active. Transmitting speech.{RESET}")

    def on_connection_status(self, status: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"{CYAN}[{timestamp}] [Network]: {status}{RESET}")


async def main_async():
    api_key = get_api_key()
    if not api_key:
        print_banner()
        print(f"\n{RED}{BOLD}[CONFIGURATION REQUIRED]{RESET}")
        print("Missing Gemini API Key. To activate J.A.R.V.I.S. Mark 56:")
        print(f"\n1. Create a {BOLD}.env{RESET} file in this directory:")
        print(f"   {CYAN}echo 'GEMINI_API_KEY=your_actual_api_key' > .env{RESET}")
        print(f"\n2. Or export it in your shell:")
        print(f"   {CYAN}export GEMINI_API_KEY=\"your_actual_api_key\"{RESET}")
        print(f"\nGet a free Gemini API key at: {BOLD}https://aistudio.google.com/{RESET}\n")
        sys.exit(1)

    print_banner()
    reporter = ConsoleReporter()

    try:
        audio_mgr = AudioManager(
            input_device_spec=AUDIO_INPUT_DEVICE,
            output_device_spec=AUDIO_OUTPUT_DEVICE,
            mute_mic_during_speech=MUTE_MIC_DURING_SPEECH,
            wake_word_enabled=WAKE_WORD_ENABLED,
            wake_word_threshold=WAKE_WORD_THRESHOLD,
            conversation_timeout=CONVERSATION_TIMEOUT,
            on_wake_detected=reporter.on_wake_detected,
            on_state_change=reporter.on_audio_state_change,
        )
    except AudioDeviceError as ade:
        print(f"\n{RED}{BOLD}[AUDIO INITIALIZATION ERROR]:{RESET} {ade}\n")
        print("Available audio devices:")
        for dev in list_audio_devices():
            print(f"  [{dev['index']}] {dev['name']} (In: {dev['max_input_channels']}, Out: {dev['max_output_channels']})")
        sys.exit(1)

    agent = JarvisAgent(
        audio_manager=audio_mgr,
        on_user_transcript=reporter.on_user_transcript,
        on_model_transcript=reporter.on_model_transcript,
        on_search_query=reporter.on_search_query,
        on_tool_call=reporter.on_tool_call,
        on_interrupted=reporter.on_interrupted,
        on_turn_complete=reporter.on_turn_complete,
        on_status_change=reporter.on_connection_status,
        on_echo_dropped=reporter.on_echo_dropped,
        on_instant_acknowledgment=reporter.on_instant_acknowledgment,
    )

    # Setup signal handlers for graceful headless daemon shutdown
    loop = asyncio.get_running_loop()

    def stop_signal():
        print(f"\n\n{GOLD}{BOLD}[J.A.R.V.I.S.]:{RESET} Shutdown signal received. Powering down Mark 56 protocols.\n")
        agent.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_signal)
        except NotImplementedError:
            pass

    try:
        await agent.run()
    except asyncio.CancelledError:
        pass


def main():
    log_level = logging.DEBUG if os.getenv("DEBUG") else logging.WARNING
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print(f"\n\n{GOLD}{BOLD}[J.A.R.V.I.S.]:{RESET} Powering down Mark 56 protocols. Good day, {USER_TITLE}.\n")
    except AudioDeviceError as ade:
        print(f"\n{RED}{BOLD}[AUDIO ERROR]:{RESET} {ade}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n{RED}{BOLD}[SYSTEM ERROR]:{RESET} {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
