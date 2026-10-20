# J.A.R.V.I.S. Mark 56 — Voice-Only AI Assistant

> *"Jarvis Mark 56 online and operational. At your service, sir."*

**J.A.R.V.I.S. Mark 56** is a **lightweight, voice-only AI assistant** powered by the **Google Gemini Live API (`gemini-3.8-live`)**. Designed for headless operation on resource-constrained hardware (such as MX Linux on an AMD dual-core laptop with 4 GB RAM), Mark 56 interacts exclusively through spoken audio—listening locally for its wake word, searching and scraping the live internet to answer questions, and responding with the wit, composure, and British elegance of Tony Stark's personal assistant.

---

## Audit & Compliance Highlights

| Requirement | Implementation & Verification Status |
|---|---|
| **1. Voice-Only** | **Verified**: Purely headless audio pipeline. No GUI, browser window, HUD, or PyQt. Runs in background or terminal. |
| **2. Gemini Live API** | **Verified**: Uses current official `gemini-3.8-live` model over bidirectional WebSockets via official `google-genai` SDK (`audio/pcm;rate=16000` in, `audio/pcm;rate=24000` out). |
| **3. Internet-Backed Answers** | **Verified**: Grounded with native **Google Search** and live **Web Scraper** (`scrape_webpage`). System instructions prohibit reading raw URLs aloud. |
| **4. Local Wake Word** | **Verified**: Uses local `openwakeword` (`hey_jarvis_v0.1.onnx`) running on CPU. Audio transmission is strictly gated: **zero network audio is transmitted while idle**. |
| **5. Chatbot Only** | **Verified**: Strictly restricted to conversational Q&A and research. No OS shell execution, application launching, or physical automation. |
| **6. Target Hardware** | **Verified**: Ultra-lightweight CPU footprint (~80–120 MB RAM, ~2–4% CPU on dual-core AMD). No GPU acceleration required. Runs smoothly on MX Linux with 4 GB RAM. |
| **7. Audio Hardware** | **Verified**: ALSA / PulseAudio compatible. Supports laptop internal microphone and speaker / AUX output (e.g. boAt Stone 350 Pro). Graceful fallback on missing devices. |
| **8. Headless Startup** | **Verified**: Includes **SysVinit** service for MX Linux (`/etc/init.d/jarvis`), **systemd** unit (`jarvis.service`), and standalone user-space watchdog (`jarvis-runner.sh`). |
| **9. Reliability & Security** | **Verified**: Automatic WebSocket reconnection with exponential backoff on network drop. API credentials are never logged (`AIzaSy...XXXX` masked). Clean SIGINT/SIGTERM shutdown. |
| **10. Verification** | **Verified**: Comprehensive automated test suite (`tests/test_audit.py`) passes 100%. |

---

## Hardware & System Requirements

- **Target System**: MX Linux (or any Debian/Ubuntu/Fedora/macOS system).
- **Processor**: AMD Dual-Core (or Intel Core 2 Duo / Celeron / ARM64) with no discrete GPU required.
- **Memory**: 4 GB RAM (JARVIS consumes < 150 MB).
- **Audio Output**: Laptop 3.5mm headphone jack connected via AUX to a **boAt Stone 350 Pro** speaker (or built-in speakers).
- **Audio Input**: Built-in laptop microphone or USB microphone.

---

## Quick Setup on Linux (MX Linux / Debian)

### 1. Install System Audio Dependencies
```bash
sudo apt update
sudo apt install -y portaudio19-dev python3-venv python3-pip
```

### 2. Set Up Virtual Environment & Dependencies
```bash
git clone <repo-url> jarvis-mark-56
cd jarvis-mark-56

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
*(Or if using `uv`: `uv venv .venv && source .venv/bin/activate && uv pip install -r requirements.txt`)*

### 3. Configure Your Gemini API Key
Copy the template and add your API key:
```bash
cp .env.example .env
nano .env
```
Fill in your key:
```env
GEMINI_API_KEY=AIzaSyYourActualKeyHere
```

---

## Configuration Reference (`.env`)

```ini
# Google Gemini API Key (Required)
GEMINI_API_KEY=your_gemini_api_key_here

# Verified Gemini Live Model
GEMINI_MODEL=gemini-3.8-live

# Prebuilt Voice (Charon = deep British articulate, Sadaltager, Fenrir)
VOICE_NAME=Charon

# Title JARVIS uses to address you
USER_TITLE=Sir

# Audio Hardware Device Selection:
# Leave blank for default ALSA / PulseAudio device.
# For boAt Stone 350 Pro via AUX, default usually maps to the 3.5mm jack.
AUDIO_INPUT_DEVICE=
AUDIO_OUTPUT_DEVICE=

# Local Wake Word ('Jarvis' or 'Hey Jarvis'):
# While idle, audio stays local on your machine.
WAKE_WORD_ENABLED=true
WAKE_WORD_THRESHOLD=0.5

# Silence timeout before returning to idle wake-word mode
CONVERSATION_TIMEOUT=12.0

# Echo suppression:
# Set to 'true' if using the boAt Stone 350 Pro without headphones
# to prevent the mic from picking up JARVIS's voice.
MUTE_MIC_DURING_SPEECH=true

# Welcome greeting upon launch
ENABLE_STARTUP_GREETING=true
```

---

## Running J.A.R.V.I.S.

### Option A: Interactive Terminal Mode
```bash
source .venv/bin/activate
python run.py
```
JARVIS will display the terminal banner, detect audio devices, and enter idle state. Say:
> *"Hey Jarvis, what is today's top space exploration news?"*

### Option B: Standalone User-Space Background Watchdog
Runs headless in the background with auto-restart on crashes. Does not require root:
```bash
bash service/jarvis-runner.sh &
```
Logs are written in real-time to `jarvis.log`:
```bash
tail -f jarvis.log
```

### Option C: MX Linux SysVinit Service (Default for MX Linux)
MX Linux uses **SysVinit** by default. To install JARVIS as an `/etc/init.d/` daemon:
```bash
sudo bash service/install.sh
```
Service control commands:
```bash
sudo service jarvis start
sudo service jarvis status
sudo service jarvis stop
sudo service jarvis restart
```
Logs are directed to `/var/log/jarvis.log`.

### Option D: Systemd Service (For systemd-enabled Linux)
If running on Debian/Ubuntu with systemd:
```bash
sudo bash service/install.sh
sudo systemctl start jarvis.service
sudo systemctl status jarvis.service
```

---

## Running Verification Tests

Run the test suite:
```bash
.venv/bin/python -m unittest tests/test_audit.py -v
```

Output:
```
test_01_voice_only_and_persona_constraints ... ok
test_02_model_id_and_live_compatibility ... ok
test_03_tools_and_chatbot_scope ... ok
test_04_local_wakeword_idle_gating ... ok
test_05_audio_manager_state_machine_and_idle_gating ... ok
test_06_audio_device_listing_and_resolution ... ok
test_07_secret_masking ... ok
test_08_startup_scripts_exist ... ok

----------------------------------------------------------------------
Ran 8 tests in 0.16s

OK
```

---

## Troubleshooting & Hardware Notes

1. **boAt Stone 350 Pro AUX Connection**:
   - Ensure the 3.5mm AUX cable is firmly connected to both the laptop headphone jack and the boAt Stone 350 Pro AUX input.
   - Switch the boAt speaker to **AUX mode** (indicated by an audio prompt or AUX LED).
   - If audio does not play through AUX, run:
     ```bash
     alsamixer
     ```
     Unmute `Headphone` and raise volume.
   - You can also test playback with:
     ```bash
     aplay -D default /usr/share/sounds/alsa/Front_Center.wav
     ```
2. **Microphone Volume on Linux**:
   - Check input levels using `alsamixer` (press F4 to view Capture controls) or `pavucontrol`.
   - Ensure the Capture slider is unmuted and at 70–80%.
3. **Echo Suppression (Speaker Mode)**:
   - When using the boAt Stone 350 Pro at moderate to high volume, set `MUTE_MIC_DURING_SPEECH=true` in `.env`. This ensures the microphone is paused while JARVIS is speaking so the speaker output does not trigger false interruptions.

---

## Architecture Flow

```
[ Ambient Microphone Audio (16kHz PCM) ]
                 |
                 v
     [ Local WakeWordDetector (CPU) ]  <--- (Idle State: ZERO Network Traffic)
                 |
     (On "Jarvis" / "Hey Jarvis")
                 v
   [ Open Real-Time WebSocket Channel ]
                 |
     +-----------+-----------+
     |                       |
     v                       v
[ Live Mic Stream ]  [ Google Search Grounding ]
     |               [ Web Scraper Tool        ]
     v                       |
[ Gemini Live Server ]       v
     |                [ Grounded Facts ]
     v
[ 24kHz Audio Output (PCM) ]
     |
     v
[ boAt Stone 350 Pro (AUX Output) ]
```

---

## License

Apache 2.0. Built with Google Gemini Live API.
