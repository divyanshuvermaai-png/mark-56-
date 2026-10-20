import os
from pathlib import Path
from dotenv import load_dotenv

# Automatically load .env file from project root
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

def get_api_key() -> str:
    """Retrieve the Gemini API key from environment variables."""
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    return key.strip() if key else ""

def get_masked_api_key() -> str:
    """Return a safely masked representation of the API key for logging."""
    key = get_api_key()
    if not key:
        return "<NOT SET>"
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"

# Model & Voice settings
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-live").strip()
VOICE_NAME = os.getenv("VOICE_NAME", "Charon").strip()
USER_TITLE = os.getenv("USER_TITLE", "Sir").strip()

# Audio Stream Configurations
INPUT_SAMPLE_RATE = 16000     # Gemini Live expects 16kHz PCM input
OUTPUT_SAMPLE_RATE = 24000    # Gemini Live returns 24kHz PCM output
AUDIO_CHANNELS = 1            # Mono audio
CHUNK_SIZE = 512              # 32ms per buffer chunk at 16kHz

# Audio Hardware Device Selection (supports device index or name substring)
AUDIO_INPUT_DEVICE = os.getenv("AUDIO_INPUT_DEVICE", "").strip()
AUDIO_OUTPUT_DEVICE = os.getenv("AUDIO_OUTPUT_DEVICE", "").strip()

# Local Wake Word Settings
WAKE_WORD_ENABLED = os.getenv("WAKE_WORD_ENABLED", "true").lower() in ("true", "1", "yes")
WAKE_WORD_THRESHOLD = float(os.getenv("WAKE_WORD_THRESHOLD", "0.5"))
CONVERSATION_TIMEOUT = float(os.getenv("CONVERSATION_TIMEOUT", "12.0"))

# Echo suppression / half-duplex behavior
MUTE_MIC_DURING_SPEECH = os.getenv("MUTE_MIC_DURING_SPEECH", "false").lower() in ("true", "1", "yes")
ENABLE_STARTUP_GREETING = os.getenv("ENABLE_STARTUP_GREETING", "true").lower() in ("true", "1", "yes")

# Grounding: Google Search Grounding requires paid/billing-enabled project on Gemini Live.
# If false, JARVIS uses built-in live web search (search_web tool via DuckDuckGo) which requires no billing.
USE_GOOGLE_SEARCH_GROUNDING = os.getenv("USE_GOOGLE_SEARCH_GROUNDING", "false").lower() in ("true", "1", "yes")

# ⚡ Instant Acknowledgment Settings
INSTANT_ACKNOWLEDGMENT_ENABLED = os.getenv("INSTANT_ACKNOWLEDGMENT_ENABLED", "true").lower() in ("true", "1", "yes")
ACKNOWLEDGMENT_VOICE_EN = os.getenv("ACKNOWLEDGMENT_VOICE_EN", "Daniel").strip()
ACKNOWLEDGMENT_VOICE_HI = os.getenv("ACKNOWLEDGMENT_VOICE_HI", "Lekha").strip()

# 📈 Alpha Vantage Financial Market Settings
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()

def get_masked_alpha_vantage_key() -> str:
    """Return a safely masked representation of the Alpha Vantage API key for logging."""
    key = ALPHA_VANTAGE_API_KEY
    if not key:
        return "<NOT SET>"
    if len(key) <= 6:
        return "****"
    return f"{key[:3]}...{key[-3:]}"



