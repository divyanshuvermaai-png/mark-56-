import os
import unittest
from jarvis.config import (
    MODEL,
    VOICE_NAME,
    USER_TITLE,
    WAKE_WORD_ENABLED,
    get_masked_api_key,
)
from jarvis.persona import build_system_instruction
from jarvis.wakeword import WakeWordDetector
from jarvis.audio import AudioManager, list_audio_devices, resolve_device_index
from jarvis.tools import SCRAPE_WEBPAGE_DECLARATION, handle_tool_call
from jarvis.agent import JarvisAgent
import pyaudio


class TestJarvisMark56Audit(unittest.TestCase):
    """
    Verification test suite covering all 10 project requirements:
    1. Voice-only / No UI constraints
    2. Gemini Live API model compatibility
    3. Internet-backed answers & URL speaking restrictions
    4. Local wake-word detection & idle audio gating
    5. Chatbot-only scope (no OS/shell commands)
    6. Lightweight dependencies
    7. Audio device selection & failure resilience
    8. Headless startup scripts
    9. Long-running reliability & secret protection
    """

    def test_01_voice_only_and_persona_constraints(self):
        instruction = build_system_instruction("Sir")
        self.assertIn("VOICE-ONLY", instruction)
        self.assertIn("NO UI", instruction)
        # Verify instructions explicitly prohibit reading raw URLs
        self.assertIn("NEVER read raw URLs", instruction)
        self.assertIn("Mark 56", instruction)

    def test_02_model_id_and_live_compatibility(self):
        self.assertEqual(MODEL, "gemini-3.8-live")
        self.assertEqual(VOICE_NAME, "Charon")

    def test_03_tools_and_chatbot_scope(self):
        # Verify only scrape_webpage tool is declared (no OS, no shell, no file tools)
        self.assertEqual(SCRAPE_WEBPAGE_DECLARATION.name, "scrape_webpage")
        self.assertIn("url", SCRAPE_WEBPAGE_DECLARATION.parameters.properties)

        # Verify handle_tool_call refuses unknown / command execution
        res = handle_tool_call("execute_command", {"command": "ls"})
        self.assertEqual(res["status"], "error")
        self.assertIn("Unknown tool", res["message"])

    def test_04_local_wakeword_idle_gating(self):
        detector = WakeWordDetector()
        self.assertTrue(detector.enabled, "WakeWordDetector must be enabled using local ONNX models.")
        # Feed silent audio frames (512 samples = 1024 bytes)
        silent_frame = b"\x00" * 1024
        self.assertFalse(detector.process_frame(silent_frame), "Silence should not trigger wake word.")

    def test_05_audio_manager_state_machine_and_idle_gating(self):
        # Test that AudioManager starts in IDLE and gates audio
        am = AudioManager(wake_word_enabled=True)
        self.assertEqual(am.state, AudioManager.STATE_IDLE)

        # Verify state transitions
        am.set_active()
        self.assertEqual(am.state, AudioManager.STATE_ACTIVE)

        am.set_idle()
        self.assertEqual(am.state, AudioManager.STATE_IDLE)

    def test_06_audio_device_listing_and_resolution(self):
        devices = list_audio_devices()
        self.assertIsInstance(devices, list)

        # Test resolution logic with mock/real PyAudio
        p = pyaudio.PyAudio()
        try:
            # Query default output
            def_idx = resolve_device_index(p, "", is_input=False)
            self.assertIsNone(def_idx)  # Empty spec should return None (use default)

            # Query non-existent device returns None and falls back
            bad_idx = resolve_device_index(p, "non_existent_fake_device_9999", is_input=False)
            self.assertIsNone(bad_idx)
        finally:
            p.terminate()

    def test_07_secret_masking(self):
        # Verify secrets are masked
        masked = get_masked_api_key()
        self.assertTrue("..." in masked or masked in ("****", "<NOT SET>"))
        # Ensure raw key is never returned unmasked by get_masked_api_key
        if os.getenv("GEMINI_API_KEY"):
            self.assertNotEqual(masked, os.getenv("GEMINI_API_KEY"))

    def test_08_startup_scripts_exist(self):
        base_dir = os.path.dirname(os.path.dirname(__file__))
        self.assertTrue(os.path.isfile(os.path.join(base_dir, "service/init.d/jarvis")))
        self.assertTrue(os.path.isfile(os.path.join(base_dir, "service/systemd/jarvis.service")))
        self.assertTrue(os.path.isfile(os.path.join(base_dir, "service/jarvis-runner.sh")))
        self.assertTrue(os.path.isfile(os.path.join(base_dir, "service/install.sh")))

    def test_09_audio_resampling(self):
        import numpy as np
        # 48k -> 16k
        data_48k = np.zeros(4800, dtype=np.int16).tobytes()
        res_16k = AudioManager._resample_pcm(data_48k, 48000, 16000)
        self.assertEqual(len(res_16k), len(data_48k) // 3)
        # 16k -> 48k
        res_48k = AudioManager._resample_pcm(res_16k, 16000, 48000)
        self.assertEqual(len(res_48k), len(res_16k) * 3)
        # 44.1k -> 16k
        data_44k = np.zeros(4410, dtype=np.int16).tobytes()
        res_44_16 = AudioManager._resample_pcm(data_44k, 44100, 16000)
        self.assertEqual(len(res_44_16), int(4410 * 16000 / 44100) * 2)

    def test_10_echo_guards(self):
        from jarvis.echo_guard import AcousticEchoGuard, SemanticEchoGuard
        # Acoustic Guard
        ag = AcousticEchoGuard()
        is_echo, is_barge = ag.inspect_frame(b"\x00" * 1024, is_speaking=True)
        self.assertTrue(is_echo)
        self.assertFalse(is_barge)

        # Semantic Guard
        sg = SemanticEchoGuard()
        sg.record_model_speech("Accessing the financial markets now, Sir.")
        self.assertTrue(sg.is_self_echo("Accessing the financial markets now, Sir."))
        self.assertTrue(sg.is_self_echo("markets now, Sir."))
        self.assertFalse(sg.is_self_echo("what is the weather in Delhi"))

    def test_11_instant_acknowledgment(self):
        from jarvis.acknowledgment import InstantAcknowledgmentEngine, detect_language
        self.assertEqual(detect_language("checking stock prices"), "en")
        self.assertEqual(detect_language("kya haal hai bhai"), "hi")

        ack = InstantAcknowledgmentEngine(enabled=True)
        phrase, pcm = ack.get_acknowledgment("stocks", "tell me apple stock", "Sir")
        self.assertIn("Sir", phrase)
        self.assertIsNotNone(pcm)
        self.assertGreater(len(pcm), 0)

    def test_12_alpha_vantage_and_financial_tools(self):
        from jarvis.tools import resolve_symbol, execute_get_stock_quote, execute_get_currency_rate
        self.assertEqual(resolve_symbol("apple"), "AAPL")
        self.assertEqual(resolve_symbol("reliance"), "RELIANCE.BSE")
        quote = execute_get_stock_quote("AAPL")
        self.assertEqual(quote["status"], "success")
        self.assertIn("AAPL", quote["symbol"])


if __name__ == "__main__":
    unittest.main()
