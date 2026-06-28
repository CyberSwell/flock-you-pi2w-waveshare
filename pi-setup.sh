#!/usr/bin/env bash
# One-time setup script for the Flock-You Pi Zero 2W deployment.
# Run as your Pi user (e.g. admin): bash pi-setup.sh
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_SRC="$REPO_DIR/api/flockyou.service"
SERVICE_DST="/etc/systemd/system/flockyou.service"
CURRENT_USER="$(whoami)"

echo "=== Flock-You Pi Setup ==="
echo "Repo: $REPO_DIR"
echo "User: $CURRENT_USER"
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
sudo apt-get install -y python3-pil python3-spidev python3-rpi.gpio git

# ── 3. Serial port access (dialout group) ────────────────────────────────────
echo "[3] Adding $CURRENT_USER to dialout group (serial port access)..."
sudo usermod -a -G dialout "$CURRENT_USER"
echo "    Group change is effective for the flockyou service immediately."
echo "    Log out and back in if you need serial access in your shell too."

# ── 4. Python packages ────────────────────────────────────────────────────────
echo "[4] Installing Python packages..."
pip3 install --break-system-packages -r "$REPO_DIR/api/requirements.txt"

# ── 5. Waveshare driver package ───────────────────────────────────────────────
# The pip package adds its lib dir to sys.path, but the driver files use
# relative imports so they must live inside a proper package. Copy them.
echo "[5] Installing Waveshare EPD drivers..."
EPAPER_LIB="$(python3 -c "import glob, os; paths=glob.glob(os.path.expanduser('~/.local/lib/python*/site-packages/epaper/e-Paper/RaspberryPi_JetsonNano/python/lib/waveshare_epd')); print(paths[0] if paths else '')")"
EPD_DEST="$REPO_DIR/api/waveshare_epd"
if [ -n "$EPAPER_LIB" ]; then
    mkdir -p "$EPD_DEST"
    cp "$EPAPER_LIB"/epd2in13_V4.py "$EPD_DEST/"
    cp "$EPAPER_LIB"/epdconfig.py    "$EPD_DEST/"
    touch "$EPD_DEST/__init__.py"
    echo "    Copied Waveshare drivers to $EPD_DEST"
else
    echo "    WARNING: waveshare_epd library not found — install waveshare-epaper first"
fi

# ── 6. systemd service ────────────────────────────────────────────────────────
echo "[6] Installing systemd service..."

# Patch WorkingDirectory and User= to match the actual clone location and user, then install
sed -e "s|/home/pi/flock-you|$REPO_DIR|g" \
    -e "s|^User=pi$|User=$CURRENT_USER|" "$SERVICE_SRC" \
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
