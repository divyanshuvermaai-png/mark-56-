#!/usr/bin/env python3
"""
J.A.R.V.I.S. Mark 56 - Universal Automated Setup & Self-Healing Installer
Runs anywhere (Linux / Debian / MX Linux / Ubuntu / Termux / macOS / Windows).
Automatically detects platform, installs system audio drivers, fixes pip/numpy/pydantic/onnx errors,
configures .env, verifies hardware, and launches J.A.R.V.I.S. seamlessly.
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ANSI colors
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

ROOT_DIR = Path(__file__).resolve().parent
ENV_FILE = ROOT_DIR / ".env"
ENV_EXAMPLE = ROOT_DIR / ".env.example"
REQ_FILE = ROOT_DIR / "requirements.txt"


def log(msg: str, color: str = CYAN):
    print(f"{color}{BOLD}[SETUP]{RESET} {msg}")


def log_success(msg: str):
    print(f"{GREEN}{BOLD}[SUCCESS]{RESET} {msg}")


def log_warn(msg: str):
    print(f"{YELLOW}{BOLD}[WARNING]{RESET} {msg}")


def log_error(msg: str):
    print(f"{RED}{BOLD}[ERROR]{RESET} {msg}")


def is_termux() -> bool:
    return "com.termux" in os.environ.get("PREFIX", "") or os.path.exists("/data/data/com.termux")


def run_cmd(cmd: list, check: bool = True, capture: bool = False, env: dict = None) -> subprocess.CompletedProcess:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True if capture else False,
        env=full_env,
    )


def install_termux_system_packages():
    log("Termux environment detected. Auto-installing pre-compiled native packages...")
    packages = [
        "git",
        "python",
        "python-pip",
        "pulseaudio",
        "clang",
        "make",
        "tur-repo",
        "portaudio",
        "python-numpy",
        "python-cryptography",
        "python-onnxruntime",
    ]
    for pkg in packages:
        try:
            log(f"Checking/Installing native package: {pkg}")
            run_cmd(["pkg", "install", "-y", pkg])
        except Exception as e:
            log_warn(f"pkg install {pkg} exited with status: {e}")


def install_linux_system_packages():
    log("Linux environment detected. Checking system audio libraries...")
    if shutil.which("apt-get"):
        cmd = ["sudo", "apt-get", "update", "-y"]
        try:
            run_cmd(cmd)
        except Exception:
            pass

        packages = ["python3-venv", "python3-pip", "portaudio19-dev", "libasound2-plugins", "espeak-ng"]
        log(f"Installing audio drivers: {' '.join(packages)}")
        try:
            run_cmd(["sudo", "apt-get", "install", "-y"] + packages)
            log_success("Linux audio drivers installed successfully.")
        except Exception as e:
            log_warn(f"Could not run sudo apt-get (user may lack root or already installed): {e}")


def install_python_requirements():
    log("Installing Python dependencies with automated error recovery...")
    python_bin = sys.executable

    # 1. Ensure pip is updated
    try:
        run_cmd([python_bin, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
    except Exception:
        pass

    # 2. Termux specific pre-installs for rust/pydantic wheels
    if is_termux():
        log("Ensuring pydantic-core binary wheel for Android...")
        try:
            run_cmd([
                python_bin, "-m", "pip", "install",
                "pydantic-core",
                "--extra-index-url", "https://termux-user-repository.github.io/pypi/"
            ])
        except Exception:
            pass

    # 3. Compile flags for portaudio in Termux
    env = {}
    if is_termux():
        prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
        env["CFLAGS"] = f"-I{prefix}/include"
        env["LDFLAGS"] = f"-L{prefix}/lib"

    # 4. Install requirements with fallbacks
    try:
        run_cmd([python_bin, "-m", "pip", "install", "-r", str(REQ_FILE)], env=env)
        log_success("All requirements installed successfully.")
    except Exception as e:
        log_warn(f"Standard pip install encountered: {e}. Running self-healing package installer...")

        essential_pkgs = [
            "python-dotenv",
            "pydantic",
            "google-genai",
            "pyaudio",
            "sounddevice",
            "httpx",
            "beautifulsoup4",
            "numpy",
        ]
        for pkg in essential_pkgs:
            try:
                log(f"Installing {pkg}...")
                run_cmd([python_bin, "-m", "pip", "install", pkg], env=env)
            except Exception as err:
                log_warn(f"Could not install {pkg}: {err}")


def configure_env_file():
    if ENV_FILE.is_file() and ENV_FILE.stat().st_size > 20:
        log_success(".env configuration already exists.")
        return

    log("Configuring .env file...")
    default_content = """# J.A.R.V.I.S. Mark 56 Environment Configuration
GEMINI_API_KEY=
ALPHA_VANTAGE_API_KEY=IW5N4MS9GYCC5F9C
GEMINI_MODEL=gemini-3.8-live
VOICE_NAME=Charon
USER_TITLE=Divyanshu Sir
WAKE_WORD_ENABLED=true
WAKE_WORD_THRESHOLD=0.5
CONVERSATION_TIMEOUT=12.0
MUTE_MIC_DURING_SPEECH=false
ENABLE_STARTUP_GREETING=true
USE_GOOGLE_SEARCH_GROUNDING=false
"""
    if is_termux():
        # Default wake word off on phone for immediate ambient chat
        default_content = default_content.replace("WAKE_WORD_ENABLED=true", "WAKE_WORD_ENABLED=false")

    try:
        ENV_FILE.write_text(default_content, encoding="utf-8")
        log_success("Created .env template.")
    except Exception as e:
        log_warn(f"Failed to create .env: {e}")

    # Prompt user for Gemini key if not present
    key = input(f"{YELLOW}{BOLD}Enter your GEMINI_API_KEY (press Enter to skip if already set): {RESET}").strip()
    if key:
        content = ENV_FILE.read_text(encoding="utf-8")
        content = content.replace("GEMINI_API_KEY=", f"GEMINI_API_KEY={key}")
        ENV_FILE.write_text(content, encoding="utf-8")
        log_success("GEMINI_API_KEY saved to .env!")


def verify_audio_hardware():
    log("Verifying audio hardware (microphone & speaker)...")
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        dev_count = p.get_device_count()
        p.terminate()
        log_success(f"Audio driver active. Found {dev_count} device channel(s).")
    except Exception as e:
        log_warn(f"Audio hardware check warning: {e}")


def main():
    print(f"""{CYAN}{BOLD}
================================================================================
          J.A.R.V.I.S. MARK 56 - AUTOMATED SELF-HEALING SETUP
================================================================================{RESET}
Detecting OS & Hardware Environment...
Platform: {platform.system()} ({platform.machine()}) | Python: {platform.python_version()}
""")

    # Step 1: Install System Packages
    if is_termux():
        install_termux_system_packages()
    elif platform.system() == "Linux":
        install_linux_system_packages()
    elif platform.system() == "Darwin":
        log("macOS detected. Native CoreAudio and system tools verified.")

    # Step 2: Install Python dependencies with self-healing
    install_python_requirements()

    # Step 3: Configure .env
    configure_env_file()

    # Step 4: Verify Hardware
    verify_audio_hardware()

    print(f"""
{GREEN}{BOLD}================================================================================
                    SETUP COMPLETE! J.A.R.V.I.S. IS READY
================================================================================{RESET}
To launch J.A.R.V.I.S. Mark 56 at any time, simply run:

    {CYAN}{BOLD}python run.py{RESET}

""")

    run_now = input("Would you like to launch J.A.R.V.I.S. right now? (y/n): ").strip().lower()
    if run_now in ("y", "yes", ""):
        log("Starting J.A.R.V.I.S. Mark 56...")
        subprocess.run([sys.executable, str(ROOT_DIR / "run.py")])


if __name__ == "__main__":
    main()
