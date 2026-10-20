#!/usr/bin/env bash
# ==============================================================================
# J.A.R.V.I.S. Mark 56 - Linux Service Installer (SysVinit & Systemd)
# Supports MX Linux (sysvinit default) and standard Linux distributions.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CURRENT_USER="$(id -un)"

echo "=== J.A.R.V.I.S. Mark 56 Service Installation ==="
echo "Target Directory: $SCRIPT_DIR"
echo "Current User:     $CURRENT_USER"

# Check if running as root
IS_ROOT=false
if [ "$(id -u)" -eq 0 ]; then
    IS_ROOT=true
fi

# Detect init system
INIT_SYSTEM="unknown"

if [ -d /run/systemd/system ]; then
    INIT_SYSTEM="systemd"
elif [ -f /etc/init.d/cron ] || [ -f /etc/init.d/rc ] || [ -d /etc/init.d ]; then
    INIT_SYSTEM="sysvinit"
fi

echo "Detected Init System: $INIT_SYSTEM"

if [ "$INIT_SYSTEM" = "sysvinit" ]; then
    echo "Configuring SysVinit service for MX Linux..."
    if [ "$IS_ROOT" = false ]; then
        echo "[!] Root privileges required to install to /etc/init.d/jarvis."
        echo "    Run: sudo bash service/install.sh"
        exit 1
    fi

    # Create /etc/init.d/jarvis script with interpolated paths
    sed -e "s|JARVIS_DIR=.*|JARVIS_DIR=\"$SCRIPT_DIR\"|" \
        -e "s|JARVIS_USER=.*|JARVIS_USER=\"$CURRENT_USER\"|" \
        "$SCRIPT_DIR/service/init.d/jarvis" > /etc/init.d/jarvis

    chmod +x /etc/init.d/jarvis
    update-rc.d jarvis defaults 99

    echo "SysVinit service installed successfully!"
    echo "Commands:"
    echo "  sudo service jarvis start"
    echo "  sudo service jarvis status"
    echo "  sudo service jarvis stop"

elif [ "$INIT_SYSTEM" = "systemd" ]; then
    echo "Configuring systemd service..."
    if [ "$IS_ROOT" = false ]; then
        echo "[!] Root privileges required to install to /etc/systemd/system/jarvis.service."
        echo "    Run: sudo bash service/install.sh"
        exit 1
    fi

    sed -e "s|User=.*|User=$CURRENT_USER|" \
        -e "s|WorkingDirectory=.*|WorkingDirectory=$SCRIPT_DIR|" \
        -e "s|ExecStart=.*|ExecStart=$SCRIPT_DIR/.venv/bin/python $SCRIPT_DIR/run.py|" \
        -e "s|EnvironmentFile=.*|EnvironmentFile=-$SCRIPT_DIR/.env|" \
        "$SCRIPT_DIR/service/systemd/jarvis.service" > /etc/systemd/system/jarvis.service

    systemctl daemon-reload
    systemctl enable jarvis.service

    echo "Systemd service installed successfully!"
    echo "Commands:"
    echo "  sudo systemctl start jarvis.service"
    echo "  sudo systemctl status jarvis.service"
    echo "  sudo systemctl stop jarvis.service"
else
    echo "No standard system service detected. Use the standalone background watchdog runner:"
    echo "  bash service/jarvis-runner.sh &"
fi
