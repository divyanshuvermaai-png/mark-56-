#!/usr/bin/env bash
# ==============================================================================
# J.A.R.V.I.S. Mark 56 - Headless Daemon Runner & Watchdog
# Runs in user space on MX Linux / Debian with auto-restart on unexpected crashes.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR" || exit 1

LOG_FILE="${JARVIS_LOG_FILE:-$SCRIPT_DIR/jarvis.log}"
PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"

if [ ! -f "$PYTHON_BIN" ]; then
    echo "[!] Virtual environment not found at $SCRIPT_DIR/.venv"
    echo "    Please create it using: uv venv .venv && source .venv/bin/activate && uv pip install -e ."
    exit 1
fi

echo "================================================================================"
echo " Starting J.A.R.V.I.S. Mark 56 Watchdog Runner"
echo " Directory: $SCRIPT_DIR"
echo " Logging to: $LOG_FILE"
echo "================================================================================"

RESTART_DELAY=3

while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting JARVIS Mark 56..." >> "$LOG_FILE"
    
    "$PYTHON_BIN" run.py >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] JARVIS exited with code $EXIT_CODE." >> "$LOG_FILE"

    # Exit code 0 or SIGINT/SIGTERM (130, 143) means clean termination
    if [ "$EXIT_CODE" -eq 0 ] || [ "$EXIT_CODE" -eq 130 ] || [ "$EXIT_CODE" -eq 143 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Clean shutdown requested. Stopping watchdog." >> "$LOG_FILE"
        break
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Unexpected termination. Restarting in ${RESTART_DELAY}s..." >> "$LOG_FILE"
    sleep "$RESTART_DELAY"
done
