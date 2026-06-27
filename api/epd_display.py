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
            import epd2in13_V4
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
        self._f_title = _load_font(_BOLD_PATHS, 14)
        self._f_body  = _load_font(_FONT_PATHS, 12)
        self._f_mono  = _load_font(_MONO_PATHS, 11)
        self._f_small = _load_font(_FONT_PATHS, 10)
        self._f_tiny  = _load_font(_FONT_PATHS, 9)

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
        draw.text((108, 4), f"{det_count} {noun}", font=self._f_body, fill=255)

        now_str = datetime.now().strftime('%H:%M')
        draw.text((212, 4), now_str, font=self._f_body, fill=255)

        # ── Connectivity row ──────────────────────────────────────────
        y = 22
        flock_ok = state.get('flock_connected', False)
        gps_ok   = state.get('gps_connected',   False)

        dot_r = 5  # circle radius
        for x_dot, label, ok, x_text in (
            (4,   "SNIFFER", flock_ok, 14),
            (112, "GPS",     gps_ok,   122),
        ):
            cy = y + dot_r
            if ok:
                draw.ellipse([x_dot, cy - dot_r, x_dot + dot_r * 2, cy + dot_r], fill=0)
            else:
                draw.ellipse([x_dot, cy - dot_r, x_dot + dot_r * 2, cy + dot_r], outline=0)
            status = "ONLINE" if ok else "OFFLINE"
            draw.text((x_text, y), f"{label}: {status}", font=self._f_small, fill=0)

        # ── Separator ─────────────────────────────────────────────────
        draw.line([0, 37, EPD_WIDTH - 1, 37], fill=0)

        # ── Latest detection ──────────────────────────────────────────
        latest_mac  = state.get('latest_mac',  '')
        latest_time = state.get('latest_time', '')
        latest_rssi = state.get('latest_rssi', '')

        if latest_mac:
            draw.text((4, 40), "LAST:", font=self._f_body, fill=0)
            draw.text((46, 40), latest_mac.upper(), font=self._f_mono, fill=0)

            draw.text((4, 54), latest_time, font=self._f_mono, fill=0)
            if latest_rssi:
                draw.text((178, 54), f"RSSI: {latest_rssi}dBm", font=self._f_small, fill=0)
        else:
            draw.text((4, 40), "No detections this session", font=self._f_body, fill=0)

        # ── Separator ─────────────────────────────────────────────────
        draw.line([0, 68, EPD_WIDTH - 1, 68], fill=0)

        # ── Session info ──────────────────────────────────────────────
        session_since = state.get('session_since', '--:--:--')
        draw.text((4, 71), f"Session since {session_since}", font=self._f_small, fill=0)

        # ── Separator ─────────────────────────────────────────────────
        draw.line([0, 84, EPD_WIDTH - 1, 84], fill=0)

        # ── Footer ────────────────────────────────────────────────────
        draw.text((4, 87), "flock-you", font=self._f_tiny, fill=0)
        footer_ts = datetime.now().strftime('%m/%d  %H:%M')
        draw.text((188, 87), footer_ts, font=self._f_tiny, fill=0)

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
