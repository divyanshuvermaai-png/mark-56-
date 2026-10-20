import logging
import os
from pathlib import Path
from typing import Optional
import numpy as np

logger = logging.getLogger("jarvis.wakeword")

try:
    from openwakeword.model import Model
    HAS_OPENWAKEWORD = True
except ImportError:
    HAS_OPENWAKEWORD = False


class WakeWordDetector:
    """
    Local, CPU-only Wake Word Detector for 'Jarvis' and 'Hey Jarvis'.
    Processes 16kHz 16-bit PCM mono audio locally while idle.
    Prevents any audio transmission over the network until activated.
    """

    def __init__(self, threshold: float = 0.5, models_dir: Optional[str] = None):
        self.threshold = threshold
        self.enabled = False
        self._model = None

        if not HAS_OPENWAKEWORD:
            logger.warning("openwakeword library not found. Wake word detection is unavailable.")
            return

        if models_dir is None:
            models_dir = str(Path(__file__).resolve().parent / "models")

        jarvis_model_path = os.path.join(models_dir, "hey_jarvis_v0.1.onnx")

        if not os.path.isfile(jarvis_model_path):
            logger.warning(
                f"Wake word model not found at {jarvis_model_path}. "
                "Wake word detection will be disabled."
            )
            return

        model_kwargs = {
            "wakeword_models": [jarvis_model_path],
            "inference_framework": "onnx",
            "ncpu": 1,
        }
        melspec_path = os.path.join(models_dir, "melspectrogram.onnx")
        embedding_path = os.path.join(models_dir, "embedding_model.onnx")
        if os.path.isfile(melspec_path):
            model_kwargs["melspec_model_path"] = melspec_path
        if os.path.isfile(embedding_path):
            model_kwargs["embedding_model_path"] = embedding_path

        try:
            self._model = Model(**model_kwargs)
            self.enabled = True
            logger.info("Local WakeWordDetector initialized ('hey_jarvis_v0.1').")
        except Exception as e:
            logger.error(f"Failed to initialize WakeWordDetector: {e}")
            self.enabled = False

    def process_frame(self, audio_bytes: bytes) -> bool:
        """
        Process a chunk of 16-bit PCM audio.
        Returns True if 'Jarvis' / 'Hey Jarvis' was detected.
        """
        if not self.enabled or self._model is None:
            return False

        try:
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
            predictions = self._model.predict(audio_array)

            for model_name, score in predictions.items():
                if score >= self.threshold:
                    logger.info(f"Wake word detected by model '{model_name}' (score: {score:.3f}).")
                    self._model.reset()
                    return True
        except Exception as e:
            logger.debug(f"Error processing audio frame in wake word detector: {e}")

        return False

    def reset(self) -> None:
        """Reset internal buffers of the wake word model."""
        if self._model:
            try:
                self._model.reset()
            except Exception:
                pass
