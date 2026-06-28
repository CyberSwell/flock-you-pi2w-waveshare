# Pi Zero 2W Deployment Guide

Complete setup for the Flock-You rig: ESP32-S3 sniffer + NEO-6M GPS + Waveshare 2.13" e-ink display on a Raspberry Pi Zero 2W.

---

## Hardware required

| Part | Notes |
|------|-------|
| Raspberry Pi Zero 2W | |
| microSD card, 8 GB+ | Class 10 / A1 or better |
| Seeed XIAO ESP32-S3 | Pre-flashed with flock-you firmware |
| NEO-6M GPS module | Breakout board with onboard LDO (accepts 3.3–5 V) |
| Waveshare 2.13" e-Paper HAT V4 | |
| USB OTG cable | micro-USB (Pi) to micro-USB (ESP32) |
| Jumper wires | For e-ink and GPS wiring |

---

## 1. Flash the SD card

Download and install **[Raspberry Pi Imager](https://www.raspberrypi.com/software/)**.

- **OS:** Raspberry Pi OS Lite (64-bit) — Bookworm
- **Storage:** your microSD card

Click the gear icon (⚙) before writing and configure:

| Setting | Value |
|---------|-------|
| Hostname | `flockpi` |
| Username | `admin` (or your preference) |
| Password | (your choice) |
| Enable SSH | ✓ |
| WiFi | optional — only needed if headless on first boot |

Write the card, insert into the Pi, power on.

---

## 2. SSH in and update

```bash
ssh admin@flockpi.local
```

```bash
sudo apt-get update && sudo apt-get upgrade -y
```

---

## 3. Wire the hardware

### NEO-6M GPS module

Power the NEO-6M from the **Pi's 5V GPIO header pin**, not the ESP32's 3.3V or 5V pin — the ESP32's USB VBUS rail can't sustain the GPS RF current draw reliably.

| NEO-6M pin | Connects to |
|------------|-------------|
| VCC | Pi GPIO **Pin 2** or **Pin 4** (5V) |
| GND | Pi GPIO any GND pin (e.g. Pin 6) |
| TX | ESP32-S3 **GPIO7** (D8 on XIAO silkscreen) |

No RX wire needed — the Pi and ESP32 only read from the module.

### ESP32-S3

Connect the ESP32-S3 to the Pi's OTG/data micro-USB port with a USB OTG cable. It appears as `/dev/ttyACM0`.

### Waveshare 2.13" e-Paper HAT V4

Connect via the labeled 8-pin edge connector or the 40-pin header. Signal mapping:

| HAT label | Pi physical pin | Pi signal |
|-----------|----------------|-----------|
| VCC | 1 | 3.3V |
| GND | 6 | GND |
| DIN | 19 | GPIO10 (MOSI) |
| CLK | 23 | GPIO11 (SCLK) |
| CS | 24 | GPIO8 (CE0) |
| DC | 22 | GPIO25 |
| RST | 11 | GPIO17 |
| BUSY | 18 | GPIO24 |

#### Pi GPIO header reference

```
Pi GPIO Header (viewed from above, pin 1 = top-left)

  ODD   EVEN
   1 [3.3V]─[ 5V ] 2    ← HAT VCC (1) / GPS VCC (2 or 4)
   3 [GP2 ]─[ 5V ] 4
   5 [GP3 ]─[ GND] 6    ← HAT GND (6)
   7 [GP4 ]─[ TX ] 8
   9 [GND ]─[ RX ]10
  11 [GP17]─[GP18]12    ← HAT RST (11)
  13 [GP27]─[ GND]14
  15 [GP22]─[GP23]16
  17 [3.3V]─[GP24]18    ← HAT BUSY (18)
  19 [MOSI]─[ GND]20    ← HAT DIN (19)
  21 [MISO]─[GP25]22    ← HAT DC (22)
  23 [SCLK]─[ CE0]24    ← HAT CLK (23) / HAT CS (24)
  25 [ GND]─[ CE1]26
```

---

## 4. Clone the repo

```bash
git clone https://github.com/CyberSwell/flock-you-pi2w-waveshare ~/flock-you
cd ~/flock-you
```

---

## 5. Run the setup script

```bash
bash pi-setup.sh
```

The script:
1. Enables SPI in `/boot/firmware/config.txt` (required for e-ink)
2. Installs system packages (`python3-pil`, `python3-spidev`, `python3-rpi.gpio`, `git`)
3. Adds your user to the `dialout` group (serial port access for `/dev/ttyACM0`)
4. Installs Python packages from `api/requirements.txt`
5. Copies Waveshare EPD drivers into `api/waveshare_epd/`
6. Installs and enables the `flockyou` systemd service

---

## 6. Reboot

A reboot is required to activate SPI (step 1 above). Always reboot after the first run:

```bash
sudo reboot
```

---

## 7. Verify

SSH back in and check the service:

```bash
sudo systemctl status flockyou
journalctl -u flockyou -f
```

You should see heartbeat lines like:

```
[flockyou] scanning (ch=6 mode=CUSTOM det=0)
{"event":"gps_status","fix":false,"latitude":null,"longitude":null,"satellites":0,"source":"esp32"}
```

`fix` becomes `true` within 1–5 minutes outdoors with clear sky view. The NEO-6M blue LED blinks while searching and pulses 1/sec on fix.

---

## 8. Access the dashboard

Open a browser on any device on the same network:

```
http://flockpi.local:5000
```

The sniffer port (`/dev/ttyACM0`) auto-connects on startup. GPS coordinates are embedded in every detection JSON once a fix is acquired.

---

## Useful commands

```bash
journalctl -u flockyou -f          # live logs
sudo systemctl restart flockyou    # restart after code changes
sudo systemctl stop flockyou       # stop the service
sudo systemctl disable flockyou    # disable auto-start on boot
sudo reboot                        # full reboot
```

---

## Troubleshooting

### `Permission denied: '/dev/ttyACM0'`
The user isn't in `dialout`. `pi-setup.sh` adds this automatically, but it only takes effect after logging out and back in for interactive shells. The systemd service picks it up immediately after the script runs.

Manual fix:
```bash
sudo usermod -a -G dialout $USER
newgrp dialout   # apply to current shell without logout
```

### NEO-6M blue LED is off / `satellites: 0` forever
The GPS module isn't getting enough power. Confirm VCC is wired to Pi GPIO Pin 2 or 4 (the Pi's 5V rail), not the ESP32's 3.3V or 5V pin.

### e-ink display blank after setup
SPI may not have been activated yet. Confirm `dtparam=spi=on` is in `/boot/firmware/config.txt` and reboot.

### Waveshare driver not found during setup
The `waveshare-epaper` pip package install may have failed silently. Run manually:
```bash
pip3 install --break-system-packages waveshare-epaper
bash pi-setup.sh   # re-run; it is safe to run multiple times
```
