"""
J.A.R.V.I.S. Mark 56 - Instant Acknowledgment Engine
⚡ Speaks a short, context-aware reply in your language the instant a longer task starts
— no more silent waiting.
"""

import hashlib
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("jarvis.acknowledgment")

# Common Hinglish / Romanized Hindi keywords for language detection
HINDI_KEYWORDS = {
    "kaun", "kya", "hai", "hain", "kaisa", "kaise", "kisi", "kisne",
    "batao", "bataiye", "bata", "karo", "kijiye", "kar", "aaj", "kal",
    "mera", "meri", "mere", "apna", "apni", "apne", "humara", "tumhara",
    "dhoondo", "khojo", "samjhao", "namaste", "shukriya", "dhanyawad",
    "theek", "accha", "achha", "nahi", "nahin", "mat", "kahan", "kab",
    "kyun", "kyu", "kaise", "kitna", "kitni", "zara", "ek", "do", "teen",
    "dekho", "dekhiye", "suno", "suniye", "chalo", "ruko", "samajh"
}

FRENCH_KEYWORDS = {"bonjour", "merci", "comment", "pourquoi", "cherche", "qui", "est", "salut"}
SPANISH_KEYWORDS = {"hola", "gracias", "busca", "quien", "como", "donde", "por", "favor"}


def detect_language(text: Optional[str]) -> str:
    """
    Detect the language of the user's speech transcript.
    Returns: 'hi' (Hindi/Hinglish), 'fr' (French), 'es' (Spanish), or 'en' (English).
    """
    if not text or not text.strip():
        return "en"

    # 1. Direct Devanagari script detection
    if re.search(r"[\u0900-\u097F]", text):
        return "hi"

    # 2. Tokenize Latin words
    words = [w.lower() for w in re.findall(r"\b[a-zA-Z]+\b", text)]
    if not words:
        return "en"

    # 3. Keyword matching for Hinglish
    hindi_count = sum(1 for w in words if w in HINDI_KEYWORDS)
    if hindi_count >= 1:
        return "hi"

    # 4. Other languages
    if any(w in FRENCH_KEYWORDS for w in words):
        return "fr"
    if any(w in SPANISH_KEYWORDS for w in words):
        return "es"

    return "en"


ACKNOWLEDGMENT_PHRASES: Dict[str, Dict[str, List[str]]] = {
    "search_web": {
        "en": [
            "Checking the web for that now, {title}.",
            "Allow me to search the archives, {title}.",
            "Looking into that online right away, {title}.",
            "Searching the network now, {title}.",
        ],
        "hi": [
            "जी {title}, अभी इंटरनेट पर देखता हूँ।",
            "बिल्कुल {title}, अभी पता लगाता हूँ।",
            "एक मिनट {title}, जानकारी खोज रहा हूँ।",
        ],
        "fr": [
            "Je recherche cela immédiatement, {title}.",
            "Un instant, {title}, je consulte les archives.",
        ],
        "es": [
            "Consultando la red de inmediato, {title}.",
            "Un momento, {title}, busco esa información.",
        ],
    },
    "scrape_webpage": {
        "en": [
            "Retrieving that webpage now, {title}.",
            "Reading the article details for you, {title}.",
            "Analyzing the site content right away, {title}.",
        ],
        "hi": [
            "जी {title}, वेबपेज पढ़कर अभी बताता हूँ।",
            "बिल्कुल {title}, साइट से जानकारी निकाल रहा हूँ।",
        ],
        "fr": [
            "J'analyse la page web dès maintenant, {title}.",
        ],
        "es": [
            "Accediendo a la página web ahora mismo, {title}.",
        ],
    },
    "stocks": {
        "en": [
            "Accessing the financial markets now, {title}.",
            "Retrieving the latest market data for you, {title}.",
            "Checking the ticker quote right away, {title}.",
            "Scanning Wall Street for {title}.",
        ],
        "hi": [
            "जी {title}, शेयर बाज़ार के ताज़ा भाव देख रहा हूँ।",
            "बिल्कुल {title}, अभी स्टॉक की जानकारी निकालता हूँ।",
            "जी {title}, मार्केट डेटा चेक कर रहा हूँ।",
        ],
        "fr": [
            "Je consulte les cours de bourse immédiatement, {title}.",
        ],
        "es": [
            "Consultando los mercados financieros ahora mismo, {title}.",
        ],
    },
    "processing": {
        "en": [
            "Right away, {title}.",
            "Processing that now, {title}.",
            "On it, {title}. Just a moment.",
            "Looking into that for you, {title}.",
        ],
        "hi": [
            "जी {title}, एक पल दीजिए।",
            "बिल्कुल {title}, अभी देखता हूँ।",
            "जी {title}, अभी तैयार करता हूँ।",
        ],
        "fr": [
            "Tout de suite, {title}.",
            "Un instant s'il vous plaît, {title}.",
        ],
        "es": [
            "Enseguida, {title}.",
            "Un momento, {title}, procesando ahora.",
        ],
    },
}


class InstantAcknowledgmentEngine:
    """
    ⚡ Instant Acknowledgment Engine:
    - Provides zero-latency (<10ms) context-aware vocal acknowledgments for longer tasks.
    - Matches the user's spoken language (English, Hindi, Hinglish, etc.).
    - Addresses the user by their custom title (e.g. 'Sir' or their name).
    - Maintains a high-speed disk and memory cache of 24kHz PCM audio buffers.
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        voice_en: str = "Daniel",
        voice_hi: str = "Lekha",
        output_sample_rate: int = 24000,
        enabled: bool = True,
    ):
        self.enabled = enabled
        self.output_sample_rate = output_sample_rate
        self.voice_en = voice_en
        self.voice_hi = voice_hi

        if cache_dir is None:
            self.cache_dir = Path(__file__).resolve().parent.parent / ".cache" / "acknowledgments"
        else:
            self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._memory_cache: Dict[str, bytes] = {}
        self._installed_voices: Optional[Dict[str, str]] = None
        self._say_available = shutil.which("say") is not None
        self._phrase_indices: Dict[str, int] = {}

    def _get_installed_voices(self) -> Dict[str, str]:
        """Query macOS say command for installed voices."""
        if self._installed_voices is not None:
            return self._installed_voices
        voices = {}
        if self._say_available:
            try:
                res = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=2.0)
                for line in res.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        voices[parts[0]] = parts[1]
            except Exception as e:
                logger.debug(f"Failed to list voices: {e}")
        self._installed_voices = voices
        return voices

    def _resolve_voice(self, lang: str) -> Optional[str]:
        """Pick the best installed voice for the target language."""
        voices = self._get_installed_voices()
        if lang == "hi":
            if self.voice_hi in voices:
                return self.voice_hi
            for v, l in voices.items():
                if "hi_" in l or "hi-" in l:
                    return v
            # Fallback to Indian English voice if pure Hindi not found
            for v, l in voices.items():
                if "en_IN" in l or "en-IN" in l:
                    return v
        else:
            if self.voice_en in voices:
                return self.voice_en
            for v, l in voices.items():
                if "en_GB" in l or "en-GB" in l:
                    return v

        return self.voice_en if self._say_available else None

    def warmup_cache(self, user_title: str = "Sir") -> None:
        """Pre-synthesizes and loads audio for all common phrases in a background thread."""
        tts_available = (
            self._say_available
            or (shutil.which("espeak-ng") is not None)
            or (shutil.which("espeak") is not None)
            or (sys.platform == "win32" and shutil.which("powershell") is not None)
        )
        if not self.enabled or not tts_available:
            return

        def _worker():
            for context, lang_dict in ACKNOWLEDGMENT_PHRASES.items():
                for lang in ("en", "hi"):
                    phrases = lang_dict.get(lang, [])
                    for phrase_template in phrases:
                        phrase = phrase_template.format(title=user_title)
                        self._get_or_synthesize_pcm(phrase, lang)

        t = threading.Thread(target=_worker, name="AckWarmupWorker", daemon=True)
        t.start()

    def _get_cache_path(self, phrase: str, lang: str) -> Path:
        """Generate a stable deterministic path for cached PCM audio."""
        h = hashlib.sha256(f"{lang}:{phrase}".encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"ack_{lang}_{h}.raw"

    def _synthesize_pcm(self, phrase: str, lang: str) -> Optional[bytes]:
        """Synthesize 24kHz 16-bit mono PCM bytes using macOS say, Linux espeak, or Windows PowerShell."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_path = tf.name

        try:
            # 1. macOS say command
            if self._say_available:
                voice = self._resolve_voice(lang)
                cmd = ["say"]
                if voice:
                    cmd.extend(["-v", voice])
                cmd.extend([
                    "--file-format=WAVE",
                    f"--data-format=LEI16@{self.output_sample_rate}",
                    "-o", wav_path,
                    phrase,
                ])

                res = subprocess.run(cmd, capture_output=True, timeout=5.0)
                if res.returncode == 0 and os.path.exists(wav_path):
                    with wave.open(wav_path, "rb") as wf:
                        return wf.readframes(wf.getnframes())

            # 2. Linux espeak-ng / espeak fallback
            espeak_bin = shutil.which("espeak-ng") or shutil.which("espeak")
            if espeak_bin:
                voice_arg = "hi" if lang == "hi" else "en-uk"
                cmd = [espeak_bin, "-v", voice_arg, "-w", wav_path, phrase]
                res = subprocess.run(cmd, capture_output=True, timeout=5.0)
                if res.returncode == 0 and os.path.exists(wav_path):
                    with wave.open(wav_path, "rb") as wf:
                        raw_frames = wf.readframes(wf.getnframes())
                        sample_rate = wf.getframerate()
                    # Resample to output rate if needed
                    if sample_rate != self.output_sample_rate:
                        from .audio import AudioManager
                        raw_frames = AudioManager._resample_pcm(raw_frames, sample_rate, self.output_sample_rate)
                    return raw_frames

            # 3. Windows PowerShell System.Speech fallback
            if sys.platform == "win32" and shutil.which("powershell"):
                safe_phrase = phrase.replace("'", "''").replace('"', '`"')
                safe_wav = wav_path.replace("\\", "/")
                ps_script = (
                    f"Add-Type -AssemblyName System.Speech; "
                    f"$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    f"$synth.SetOutputToWaveFile('{safe_wav}'); "
                    f"$synth.Speak('{safe_phrase}'); "
                    f"$synth.Dispose();"
                )
                res = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                    capture_output=True,
                    timeout=5.0,
                )
                if res.returncode == 0 and os.path.exists(wav_path):
                    with wave.open(wav_path, "rb") as wf:
                        raw_frames = wf.readframes(wf.getnframes())
                        sample_rate = wf.getframerate()
                    if sample_rate != self.output_sample_rate:
                        from .audio import AudioManager
                        raw_frames = AudioManager._resample_pcm(raw_frames, sample_rate, self.output_sample_rate)
                    return raw_frames

            return None

        except Exception as e:
            logger.debug(f"Speech synthesis error for '{phrase}': {e}")
            return None
        finally:
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

    def _get_or_synthesize_pcm(self, phrase: str, lang: str) -> Optional[bytes]:
        """Check memory cache -> disk cache -> synthesize."""
        cache_key = f"{lang}:{phrase}"
        if cache_key in self._memory_cache:
            return self._memory_cache[cache_key]

        disk_path = self._get_cache_path(phrase, lang)
        if disk_path.is_file():
            try:
                pcm = disk_path.read_bytes()
                if pcm:
                    self._memory_cache[cache_key] = pcm
                    return pcm
            except Exception as e:
                logger.debug(f"Failed to read cached audio: {e}")

        # Synthesize on demand
        pcm = self._synthesize_pcm(phrase, lang)
        if pcm:
            self._memory_cache[cache_key] = pcm
            try:
                disk_path.write_bytes(pcm)
            except Exception as e:
                logger.debug(f"Failed to cache audio on disk: {e}")
            return pcm

        return None

    def get_acknowledgment(
        self,
        tool_name: str,
        user_text: Optional[str] = None,
        user_title: str = "Sir",
    ) -> Tuple[str, Optional[bytes]]:
        """
        Produce an instant, context-aware reply in the user's language.
        Returns:
            (phrase_text, pcm_bytes)
        """
        if not self.enabled:
            return "", None

        # 1. Map tool/context name
        if tool_name == "search_web":
            context = "search_web"
        elif tool_name == "scrape_webpage":
            context = "scrape_webpage"
        elif tool_name in ("get_stock_quote", "get_currency_exchange_rate"):
            context = "stocks"
        else:
            context = "processing"

        # 2. Detect language
        lang = detect_language(user_text)

        # 3. Select phrase list
        context_phrases = ACKNOWLEDGMENT_PHRASES.get(context, ACKNOWLEDGMENT_PHRASES["processing"])
        phrases = context_phrases.get(lang) or context_phrases.get("en", [])
        if not phrases:
            return "", None

        # 4. Rotate or pick phrase smoothly
        rot_key = f"{context}_{lang}"
        idx = self._phrase_indices.get(rot_key, 0)
        selected_template = phrases[idx % len(phrases)]
        self._phrase_indices[rot_key] = idx + 1

        phrase_text = selected_template.format(title=user_title)

        # 5. Retrieve pre-cached or synthesized PCM
        pcm_bytes = self._get_or_synthesize_pcm(phrase_text, lang)
        return phrase_text, pcm_bytes
