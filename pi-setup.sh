#!/usr/bin/env bash
# One-time setup script for the Flock-You Pi Zero 2W deployment.
# Run as the 'pi' user: bash pi-setup.sh
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_SRC="$REPO_DIR/api/flockyou.service"
SERVICE_DST="/etc/systemd/system/flockyou.service"

echo "=== Flock-You Pi Setup ==="
echo "Repo: $REPO_DIR"
echo ""

# ── 1. Enable SPI (required for e-paper HAT) ─────────────────────────────────
# Pi OS Bookworm uses /boot/firmware/config.txt; Bullseye uses /boot/config.txt
BOOT_CFG="/boot/firmware/config.txt"
[ -f "$BOOT_CFG" ] || BOOT_CFG="/boot/config.txt"

if ! grep -q "^dtparam=spi=on" "$BOOT_CFG" 2>/dev/null; then
    echo "dtparam=spi=on" | sudo tee -a "$BOOT_CFG"
    echo "[1] SPI enabled in $BOOT_CFG"
    echo "    NOTE: a reboot is required before the display will work."
else
    echo "[1] SPI already enabled in $BOOT_CFG"
fi

# ── 2. System packages ────────────────────────────────────────────────────────
echo "[2] Installing system packages..."
sudo apt-get update -q
sudo apt-get install -y python3-pil python3-spidev python3-rpi.gpio

# ── 3. Python packages ────────────────────────────────────────────────────────
echo "[3] Installing Python packages..."
pip3 install --break-system-packages -r "$REPO_DIR/api/requirements.txt"

# ── 4. systemd service ────────────────────────────────────────────────────────
echo "[4] Installing systemd service..."

# Patch WorkingDirectory to match the actual clone location, then install
sed "s|/home/pi/flock-you|$REPO_DIR|g" "$SERVICE_SRC" \
    | sudo tee "$SERVICE_DST" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable flockyou
sudo systemctl restart flockyou

echo ""
echo "=== Done ==="
echo "Service status:"
sudo systemctl status flockyou --no-pager

echo ""
echo "Useful commands:"
echo "  journalctl -u flockyou -f        # live logs"
echo "  sudo systemctl restart flockyou  # restart after code changes"
echo "  sudo reboot                       # reboot to activate SPI if just enabled"
