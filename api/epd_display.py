"""
Waveshare 2.13" e-Paper HAT V4 display for Flock-You Pi deployment.

Runs as a background daemon thread. Reads Flask-app state via a caller-supplied
state_getter() callable so it stays decoupled from the Flask globals.

Layout (250 × 122 px, landscape):
  ┌──────────────────────────────────────────────────┐
  │ [BLACK] FLOCK-YOU       42 devices      14:23    │  0-19
  ├──────────────────────────────────────────────────┤
  │ ● SNIFFER: ONLINE   ○ GPS: NO FIX               │  21-36
  ├──────────────────────────────────────────────────┤
  │ LAST: AA:BB:CC:DD:EE:FF                          │  39-51
  │ 2026-06-26 14:23:05              RSSI: -67 dBm   │  53-65
  ├──────────────────────────────────────────────────┤
  │ Session since 14:00:00                           │  68-80
  ├──────────────────────────────────────────────────┤
  │ flock-you                        06/26  14:25   │  83-95
  └──────────────────────────────────────────────────┘

Hardware: HAT sits on Pi 40-pin header; BCM pins are fixed by Waveshare.
Library:  pip install waveshare-epaper   (Pi only; no-ops on other platforms)
"""

import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger(__name__)

EPD_WIDTH  = 250
EPD_HEIGHT = 122

_UPDATE_INTERVAL_S = 5      # poll for state changes this often
_FULL_REFRESH_S    = 1800   # force full refresh every 30 min (anti-ghosting)

# TrueType font paths tried in order; falls back to PIL bitmap if none found.
_FONT_PATHS = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
]
_BOLD_PATHS = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
]
_MONO_PATHS = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf',
    '/usr/share/fonts/truetype/freefont/FreeMono.ttf',
]
_MONO_BOLD_PATHS = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf',
]


def _load_font(paths, size):
    try:
        from PIL import ImageFont
        for p in paths:
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                pass
        return ImageFont.load_default()
    except ImportError:
        return None


class EPDDisplay:
    """Non-blocking e-paper display manager.

    Usage::

        display = EPDDisplay(state_getter)
        display.start()          # call once at startup
        ...
        display.stop()           # call on shutdown
    """

    def __init__(self, state_getter):
        """
        Args:
            state_getter: callable → dict with keys:
                det_count (int), flock_connected (bool), gps_connected (bool),
                latest_mac (str), latest_time (str), latest_rssi (str),
                session_since (str)
        """
        self._state_getter = state_getter
        self._thread        = None
        self._stop          = threading.Event()
        self._epd           = None
        self._last_state    = None
        self._last_full_at  = 0.0

        # Font handles (set by _load_fonts)
        self._f_title = None
        self._f_body  = None
        self._f_mono  = None
        self._f_small = None
        self._f_tiny  = None

    # ------------------------------------------------------------------ #
    # Public lifecycle                                                     #
    # ------------------------------------------------------------------ #

    def start(self):
        """Start the background display thread. Safe to call if hardware is absent."""
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name='epd-display'
        )
        self._thread.start()
        logger.info("EPD display thread started")

    def stop(self):
        """Signal the display thread to exit and put the panel to sleep."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=12)
        if self._epd:
            try:
                self._epd.sleep()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _init_epd(self):
        """Try to open the Waveshare V4 driver. Returns True on success."""
        try:
            from waveshare_epd import epd2in13_V4
            self._epd = epd2in13_V4.EPD()
            self._epd.init()
            self._epd.Clear()
            logger.info('Waveshare 2.13" V4 EPD initialized')
            return True
        except ImportError:
            logger.warning(
                "waveshare_epd not found — install with: "
                "pip install waveshare-epaper\n"
                "Or copy the Waveshare library to api/waveshare_epd/\n"
                "Display output will be skipped."
            )
        except Exception as exc:
            logger.error("EPD hardware init failed: %s", exc)
        return False

    def _load_fonts(self):
        self._f_title = _load_font(_MONO_BOLD_PATHS, 12)
        self._f_body  = _load_font(_MONO_PATHS,      12)
        self._f_mono  = _load_font(_MONO_PATHS,      11)
        self._f_small = _load_font(_MONO_PATHS,      11)
        self._f_tiny  = _load_font(_MONO_PATHS,      11)

    def _render(self, state):
        """Return a PIL Image representing the current state."""
        from PIL import Image, ImageDraw

        img  = Image.new('1', (EPD_WIDTH, EPD_HEIGHT), 255)  # white canvas
        draw = ImageDraw.Draw(img)

        # ── Title bar (black background, white text) ──────────────────
        draw.rectangle([0, 0, EPD_WIDTH - 1, 19], fill=0)
        draw.text((4, 3), "FLOCK-YOU", font=self._f_title, fill=255)

        det_count = state.get('det_count', 0)
        noun = "device" if det_count == 1 else "devices"
        device_str = f"{det_count} {noun}"
        now_str = datetime.now().strftime('%m/%d %H:%M')
        try:
            dev_w = int(draw.textlength(device_str, font=self._f_body))
            now_w = int(draw.textlength(now_str,    font=self._f_body))
            draw.text(((EPD_WIDTH - dev_w) // 2, 4), device_str, font=self._f_body, fill=255)
            draw.text((EPD_WIDTH - now_w - 4,    4), now_str,    font=self._f_body, fill=255)
        except AttributeError:
            draw.text((108, 4), device_str, font=self._f_body, fill=255)
            draw.text((170, 4), now_str,    font=self._f_body, fill=255)

        # ── Sniffer status (detection section header) ─────────────────
        flock_ok = state.get('flock_connected', False)
        gps_ok   = state.get('gps_connected',   False)
        dot_r = 5
        y_sniff = 25
        cy = y_sniff + dot_r
        if flock_ok:
            draw.ellipse([4, cy - dot_r, 4 + dot_r * 2, cy + dot_r], fill=0)
        else:
            draw.ellipse([4, cy - dot_r, 4 + dot_r * 2, cy + dot_r], outline=0)
        draw.text((18, y_sniff), f"SNIFFER: {'ONLINE' if flock_ok else 'OFFLINE'}",
                  font=self._f_mono, fill=0)

        # ── Latest detection ──────────────────────────────────────────
        latest_mac     = state.get('latest_mac',     '')
        latest_age     = state.get('latest_age',     '')
        latest_rssi    = state.get('latest_rssi',    '')
        latest_channel = state.get('latest_channel', '')

        if latest_mac:
            draw.text((4,  42), "LAST:",             font=self._f_mono, fill=0)
            draw.text((46, 42), latest_mac.upper(),  font=self._f_mono, fill=0)

            draw.text((4, 59), latest_age, font=self._f_small, fill=0)
            if latest_channel:
                draw.text((100, 59), f"CH {latest_channel}", font=self._f_small, fill=0)
            if latest_rssi:
                rssi_str = f"RSSI: {latest_rssi}dBm"
                try:
                    rssi_w = int(draw.textlength(rssi_str, font=self._f_mono))
                    draw.text((EPD_WIDTH - rssi_w - 4, 59), rssi_str, font=self._f_mono, fill=0)
                except AttributeError:
                    draw.text((155, 59), rssi_str, font=self._f_mono, fill=0)
        else:
            draw.text((4, 42), "No detections this session", font=self._f_mono, fill=0)

        # ── Separator ─────────────────────────────────────────────────
        draw.line([0, 78, EPD_WIDTH - 1, 78], fill=0)

        # ── GPS status + coordinates (hidden when GPS is offline) ──────
        gps_lat = state.get('gps_lat', '')
        gps_lon = state.get('gps_lon', '')
        cy = 84 + dot_r
        if gps_ok:
            draw.ellipse([4, cy - dot_r, 4 + dot_r * 2, cy + dot_r], fill=0)
            if isinstance(gps_lat, (int, float)) and isinstance(gps_lon, (int, float)):
                draw.text((18, 84), f"GPS: {gps_lat:.4f} / {gps_lon:.4f}",
                          font=self._f_mono, fill=0)
            else:
                draw.text((18, 84), "GPS: Searching...", font=self._f_mono, fill=0)
        else:
            draw.ellipse([4, cy - dot_r, 4 + dot_r * 2, cy + dot_r], outline=0)
            draw.text((18, 84), "GPS: OFFLINE", font=self._f_mono, fill=0)

        # ── Stats row (sats + cumulative total) ───────────────────────
        gps_sats  = state.get('gps_sats', 0)
        cum_count = state.get('cumulative_count', 0)
        sats_str  = f"{gps_sats} sats" if (gps_ok and gps_sats) else "No fix"
        draw.text((4,   101), sats_str,                font=self._f_small, fill=0)
        draw.text((130, 101), f"Total: {cum_count:,}", font=self._f_small, fill=0)


        return img

    def _push(self, img, full=False):
        if not self._epd:
            return
        try:
            buf = self._epd.getbuffer(img)
            if full:
                self._epd.init()
                self._epd.Clear()
                self._epd.display(buf)
            else:
                self._epd.display(buf)
        except Exception as exc:
            logger.error("EPD push error: %s", exc)

    def _run(self):
        if not self._init_epd():
            return
        self._load_fonts()
        self._last_full_at = time.monotonic()

        # Initial render — always a full refresh on boot
        try:
            state = self._state_getter()
            self._push(self._render(state), full=True)
            self._last_state = dict(state)
        except Exception as exc:
            logger.error("EPD initial render error: %s", exc)

        while not self._stop.wait(_UPDATE_INTERVAL_S):
            try:
                state = self._state_getter()
                now   = time.monotonic()
                need_full = (now - self._last_full_at) >= _FULL_REFRESH_S

                if state != self._last_state or need_full:
                    self._push(self._render(state), full=need_full)
                    self._last_state = dict(state)
                    if need_full:
                        self._last_full_at = now
            except Exception as exc:
                logger.error("EPD render loop error: %s", exc)
