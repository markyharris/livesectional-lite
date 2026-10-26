#!/usr/bin/env python3
"""metar_lite.py - LiveSectional Lite display program.

Fetches the Aviation Weather Center cache file (ALL current METARs in one
gzip'd XML, regenerated every minute) instead of polling the API per
station, and drives a WS281x LED strip: one LED per airport position from
the `airports` file. METARs only - no TAF, no MOS.

Run `python3 metar_lite.py --once` for a single fetch + render cycle
(console simulation when rpi_ws281x is not installed, e.g. off the Pi).
Run with no arguments for the normal display loop.
"""

import argparse
import gzip
import json
import logging
import os
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
AIRPORTS_FILE = os.path.join(HERE, "airports")
CONFIG_FILE = os.path.join(HERE, "config.json")
STATUS_FILE = os.path.join(HERE, "status.json")

# The web admin drops this flag file when Reboot/Shutdown is pressed so
# the strip blanks immediately instead of freezing on its last frame
# (a halted Pi leaves a powered strip lit). The display service removes
# the flag at startup, so a reboot brings the map back lit.
POWER_BLANK_FILE = os.path.join(HERE, "leds_off.flag")

# The web admin's map builder writes this file to ask the display loop
# to light a single LED ({"position": n, "active": true}) so the builder
# can see which airport belongs at that position while editing the
# airports file. Only this process ever drives the strip.
BUILDER_FILE = os.path.join(HERE, "builder.json")
BUILDER_COLOR = (255, 255, 255)  # bright white, stands out from wx colors

CACHE_URL = "https://aviationweather.gov/data/cache/metars.cache.xml.gz"
FETCH_INTERVAL = 300          # seconds between cache-file fetches (5 min)
FETCH_TIMEOUT = 30            # seconds
USER_AGENT = "livesectional-lite/1.0 (personal LED weather map)"
RENDER_INTERVAL = 0.25        # seconds between blink/flash re-renders

WIND_THRESHOLD_KT = 15        # high-wind blink at/above this speed or gust
NIGHT_BRIGHTNESS_FACTOR = 0.25
WIPE_SECONDS = 1.5            # total duration of the single wipe transition
BLANK_SECONDS = 0.5           # dark beat before the wipe, so updates show

# LED strip configuration (mirrors livesectional config.py / metar-v4.py)
LED_COUNT = 183               # positions in the airports file
LED_PIN = 18                  # GPIO pin (PWM)
LED_FREQ_HZ = 800000
LED_DMA = 5
LED_INVERT = False
LED_CHANNEL = 0

# Standard LiveSectional flight-category colors
COLORS = {
    "VFR": (0, 255, 0),       # green
    "MVFR": (0, 0, 255),      # blue
    "IFR": (255, 0, 0),       # red
    "LIFR": (255, 0, 255),    # magenta
}
OFF = (0, 0, 0)
NO_DATA = (32, 32, 32)        # dim white ("silver") - station not reporting
FLASH_COLOR = (255, 255, 200)  # bright warm white for lightning

# Legend (LGND) LEDs. A legend position can name its color in the
# airports file - LGND:VFR, LGND:NODATA, LGND:WIND, LGND:TS - so it
# always matches the printed legend card next to it, wherever it sits
# in the file. A bare LGND takes the next color in the LEGEND sequence
# (the order below, which mirrors the original map's legend row).
LEGEND_TAGS = {
    "VFR": COLORS["VFR"],
    "MVFR": COLORS["MVFR"],
    "IFR": COLORS["IFR"],
    "LIFR": COLORS["LIFR"],
    "NODATA": NO_DATA,
    "WIND": (255, 255, 0),      # yellow
    "TS": (255, 255, 255),      # white - thunderstorms in the area
}
LEGEND = [LEGEND_TAGS[tag] for tag in
          ("VFR", "MVFR", "IFR", "LIFR", "NODATA", "WIND", "TS")]

DEFAULT_CONFIG = {
    "brightness": 60,         # percent, 0-100
    "night_mode": True,
    "night_start": "22:00",   # local time
    "night_end": "06:00",     # local time
    "night_brightness": 25,   # at night, scale to this % of brightness (0 = off)
    "high_wind": True,
    "lightning": True,
    "use_wipe": True,         # blank + wipe transition on each update
    "map_on": True,           # master on/off, toggled from the admin page
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("metar_lite")


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def load_config():
    """Read config.json, falling back to defaults for anything missing."""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError) as e:
        log.warning("config read failed (%s); using defaults", e)
    try:
        cfg["brightness"] = max(0, min(100, int(cfg["brightness"])))
    except (TypeError, ValueError):
        cfg["brightness"] = DEFAULT_CONFIG["brightness"]
    return cfg


def effective_brightness(cfg, now=None):
    """Brightness percent after the night-mode reduction, if in window.

    At night the brightness scales to cfg['night_brightness'] percent of
    the normal level (default 25, the old fixed factor). Setting it to 0
    turns the map completely off overnight."""
    pct = cfg["brightness"]
    if cfg.get("night_mode") and _in_night_window(cfg, now or datetime.now()):
        try:
            night_pct = max(0, min(100, int(cfg.get("night_brightness", 25))))
        except (TypeError, ValueError):
            night_pct = int(NIGHT_BRIGHTNESS_FACTOR * 100)
        pct = pct * night_pct / 100.0
    return pct


def _in_night_window(cfg, now):
    def parse(hhmm):
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    try:
        start, end = parse(cfg["night_start"]), parse(cfg["night_end"])
    except (KeyError, ValueError):
        return False
    cur = now.hour * 60 + now.minute
    if start <= end:                       # e.g. 01:00-05:00
        return start <= cur < end
    return cur >= start or cur < end       # wraps midnight, e.g. 22:00-06:00


# --------------------------------------------------------------------------
# Airports layout
# --------------------------------------------------------------------------
def load_airports():
    """Return the ordered LED layout: station IDs, 'NULL', or 'LGND'."""
    with open(AIRPORTS_FILE) as f:
        return [line.strip() for line in f if line.strip()]


def maybe_reload_layout(layout, strip, mtime):
    """Reload the airports file if it changed on disk (the web admin's
    map builder edits it live). Recreates the strip if the position
    count changed. Returns (layout, strip, mtime)."""
    try:
        cur = os.path.getmtime(AIRPORTS_FILE)
    except OSError:
        return layout, strip, mtime
    if mtime is not None and cur == mtime:
        return layout, strip, mtime
    try:
        new_layout = load_airports()
    except OSError as e:
        log.warning("airports reload failed (%s); keeping old layout", e)
        return layout, strip, cur
    if len(new_layout) != len(layout):
        strip = make_strip(len(new_layout))
    if new_layout != layout:
        log.info("airports file changed - layout reloaded (%d positions)",
                 len(new_layout))
    return new_layout, strip, cur


_builder_cache = {"mtime": None, "state": None}


def load_builder():
    """Builder state from the web admin ({'position': n, 'active': bool})
    or None. Cached by file mtime; the file is tiny and written rarely."""
    try:
        cur = os.path.getmtime(BUILDER_FILE)
    except OSError:
        _builder_cache.update(mtime=None, state=None)
        return None
    if _builder_cache["mtime"] == cur:
        return _builder_cache["state"]
    state = None
    try:
        with open(BUILDER_FILE) as f:
            saved = json.load(f)
        state = {"position": int(saved.get("position", 0)),
                 "active": bool(saved.get("active", False))}
    except (OSError, ValueError, TypeError):
        state = None
    _builder_cache.update(mtime=cur, state=state)
    return state


def builder_active(builder, layout):
    """True when the builder light should be showing for this layout."""
    return bool(builder and builder.get("active")
                and 0 <= builder.get("position", -1) < len(layout))


def frame_brightness(cfg, builder, layout):
    """Brightness percent for the next frame: full while the builder
    light is on (you're standing at the map looking for one LED),
    otherwise the normal day/night level."""
    if builder_active(builder, layout):
        return 100.0
    return effective_brightness(cfg)


# --------------------------------------------------------------------------
# Cache-file fetch + parse
# --------------------------------------------------------------------------
def fetch_metars(wanted):
    """Download the AWC cache file and return {station_id: metar dict}
    for stations in `wanted`, newest report per station. None on failure."""
    req = urllib.request.Request(CACHE_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw = gzip.decompress(resp.read())
        root = ET.fromstring(raw)
    except Exception as e:  # network, gzip, or XML problem: keep old data
        log.error("cache fetch/parse failed: %s", e)
        return None

    metars = {}
    for el in root.iter("METAR"):
        sid = _text(el, "station_id")
        if sid not in wanted:
            continue
        obs_time = _text(el, "observation_time") or ""
        if sid in metars and metars[sid]["obs_time"] >= obs_time:
            continue  # keep only the newest report per station
        vis = _vis(_text(el, "visibility_statute_mi"))
        wx = _text(el, "wx_string") or ""
        category = _text(el, "flight_category")
        if category not in COLORS:
            category = compute_category(vis, ceiling_ft(el))
        metars[sid] = {
            "obs_time": obs_time,
            "wind_kt": _int(_text(el, "wind_speed_kt")),
            "gust_kt": _int(_text(el, "wind_gust_kt")),
            "vis": vis,
            "wx": wx,
            "category": category,
        }
    log.info("cache ok: %d of %d mapped stations reporting",
             len(metars), len(wanted))
    return metars


def ceiling_ft(metar_el):
    """Lowest broken/overcast layer base in feet AGL, or None."""
    bases = []
    for sky in metar_el.findall("sky_condition"):
        if sky.get("sky_cover") in ("BKN", "OVC", "OVX"):
            base = _int(sky.get("cloud_base_ft_agl"))
            if base is not None:
                bases.append(base)
    return min(bases) if bases else None


def compute_category(vis, ceiling):
    """Flight category from visibility (SM) and ceiling (ft) per FAA rules."""
    if (vis is not None and vis < 1) or (ceiling is not None and ceiling < 500):
        return "LIFR"
    if (vis is not None and vis < 3) or (ceiling is not None and ceiling < 1000):
        return "IFR"
    if (vis is not None and vis <= 5) or (ceiling is not None and ceiling <= 3000):
        return "MVFR"
    return "VFR"


def _text(el, tag):
    child = el.find(tag)
    return child.text.strip() if child is not None and child.text else None


def _int(s):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _vis(s):
    """Visibility in statute miles. AWC encodes 10+ as '10+', and values
    can occasionally appear as fractions ('1/2'); handle both."""
    if s is None:
        return None
    s = s.strip().rstrip("+")
    if "/" in s:
        num, _, den = s.partition("/")
        try:
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return None
    return _float(s)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def compute_frame(layout, metars, cfg, t=None, builder=None):
    """Return (colors, symbols): one (r,g,b) and one sim-mode character
    per LED position, for time `t` (blink/flash phase)."""
    t = time.time() if t is None else t
    if os.path.exists(POWER_BLANK_FILE):
        # A power action is in progress: every LED dark, no exceptions.
        return [OFF] * len(layout), ["."] * len(layout)
    if builder_active(builder, layout):
        # Map builder: darken the whole string and light just the one
        # position being edited, so the builder can see which airport
        # on the physical map it is. Overrides the weather display and
        # the map on/off switch; fetching continues underneath.
        colors = [OFF] * len(layout)
        symbols = ["."] * len(layout)
        colors[builder["position"]] = BUILDER_COLOR
        symbols[builder["position"]] = "B"
        return colors, symbols
    if not cfg.get("map_on", True):
        # Map switched off from the admin page: every LED dark, legend
        # included. Data keeps fetching, so turning it back on is instant.
        return [OFF] * len(layout), ["."] * len(layout)
    blink_on = int(t) % 2 == 0                    # ~1 s high-wind blink cycle
    flash_on = (t % 1.0) < 0.12 or 0.25 <= (t % 1.0) < 0.37  # double flash
    colors, symbols = [], []
    legend_i = 0
    for pos in layout:
        if pos == "NULL":
            colors.append(OFF); symbols.append("."); continue
        if pos == "LGND" or pos.startswith("LGND:"):
            tag = pos[5:] if pos.startswith("LGND:") else ""
            colors.append(LEGEND_TAGS.get(tag)
                          or LEGEND[legend_i % len(LEGEND)])
            symbols.append("G"); legend_i += 1; continue
        m = metars.get(pos)
        if m is None:
            colors.append(NO_DATA); symbols.append("N"); continue
        if cfg.get("lightning") and ("TS" in m["wx"] or "LTNG" in m["wx"]):
            if flash_on:
                colors.append(FLASH_COLOR); symbols.append("*")
            else:
                colors.append(OFF); symbols.append(".")
            continue
        windy = max(m["wind_kt"] or 0, m["gust_kt"] or 0) >= WIND_THRESHOLD_KT
        if cfg.get("high_wind") and windy and not blink_on:
            colors.append(OFF); symbols.append("."); continue
        colors.append(COLORS[m["category"]])
        symbols.append(m["category"][0])          # V, M, I, or L
    return colors, symbols


def scale(color, brightness_pct):
    f = brightness_pct / 100.0
    return tuple(int(c * f) for c in color)


# --------------------------------------------------------------------------
# Strip drivers
# --------------------------------------------------------------------------
class PiStrip:
    """Real WS281x strip via rpi_ws281x (Raspberry Pi only)."""

    def __init__(self, count):
        from rpi_ws281x import PixelStrip, ws
        self.strip = PixelStrip(count, LED_PIN, LED_FREQ_HZ, LED_DMA,
                                LED_INVERT, 255, LED_CHANNEL,
                                ws.WS2811_STRIP_GRB)
        self.strip.begin()
        self.count = count

    def show(self, colors, brightness_pct):
        # Brightness is applied by scaling pixel values (the library's own
        # setBrightness is global state we don't need here).
        for i, c in enumerate(colors):
            r, g, b = scale(c, brightness_pct)
            self.strip.setPixelColorRGB(i, r, g, b)
        self.strip.show()

    def wipe(self, colors, brightness_pct, symbols=None):
        """The update transition: blank the whole string for a beat,
        then a front sweeps it, each LED switching to its new color as
        the front passes. The blank beat makes an update visible even
        when most colors didn't change."""
        for i in range(self.count):
            self.strip.setPixelColorRGB(i, 0, 0, 0)
        self.strip.show()
        time.sleep(BLANK_SECONDS)
        delay = WIPE_SECONDS / self.count
        for i, c in enumerate(colors):
            r, g, b = scale(c, brightness_pct)
            self.strip.setPixelColorRGB(i, r, g, b)
            self.strip.show()
            time.sleep(delay)


class SimStrip:
    """Console fallback: prints one compact line of category letters."""

    def __init__(self, count):
        self.count = count

    def show(self, colors, brightness_pct, symbols=None):
        line = "".join(symbols) if symbols else "?" * self.count
        print(f"[{datetime.now():%H:%M:%S}] bright={brightness_pct:5.1f}% {line}",
              flush=True)

    def wipe(self, colors, brightness_pct, symbols=None):
        print(f"--- blank ({BLANK_SECONDS:.1f}s) -> wipe "
              f"({WIPE_SECONDS:.1f}s) -> new data ---", flush=True)
        self.show(colors, brightness_pct, symbols)


def make_strip(count):
    try:
        import rpi_ws281x  # noqa: F401
    except ImportError:
        log.info("rpi_ws281x not available - console simulation mode")
        return SimStrip(count)
    try:
        strip = PiStrip(count)
    except RuntimeError as e:
        # The library is installed but the hardware init failed -
        # almost always because the process isn't root (ws2811_init
        # needs /dev/mem). Fall back instead of crashing, so the
        # --once console test works for an unprivileged user too.
        log.warning("LED hardware could not be initialised (%s) - "
                    "console simulation mode; the real strip needs "
                    "root (sudo)", e)
        return SimStrip(count)
    log.info("rpi_ws281x found - driving the real strip")
    return strip


# --------------------------------------------------------------------------
# Status (read by webapp.py)
# --------------------------------------------------------------------------
def write_status(metars, total):
    counts = {cat: 0 for cat in COLORS}
    for m in metars.values():
        if m["category"] in counts:
            counts[m["category"]] += 1
    status = {
        "last_fetch": datetime.now().astimezone().isoformat(timespec="seconds"),
        "stations_reporting": len(metars),
        "stations_total": total,
        "counts": counts,  # reporting stations per flight category
    }
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(status, f)
    except OSError as e:
        log.warning("could not write status file: %s", e)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def run_once(strip, layout, cfg, builder=None):
    """One fetch + wipe cycle. Returns the METAR dict ({} on failure)."""
    wanted = {p for p in layout
              if p != "NULL" and not p.startswith("LGND")}
    metars = fetch_metars(wanted)
    if metars is None:
        return {}
    pct = frame_brightness(cfg, builder, layout)
    colors, symbols = compute_frame(layout, metars, cfg, builder=builder)
    if cfg.get("use_wipe", True):
        strip.wipe(colors, pct, symbols)
    elif isinstance(strip, SimStrip):
        strip.show(colors, pct, symbols)
    else:
        strip.show(colors, pct)
    write_status(metars, len(wanted))
    return metars


def main():
    ap = argparse.ArgumentParser(description="Minimal METAR LED map")
    ap.add_argument("--once", action="store_true",
                    help="fetch and render a single cycle, then exit")
    args = ap.parse_args()

    # A power-blank flag left over from a reboot request has done its
    # job once we are running again: clear it so the map lights normally.
    try:
        os.remove(POWER_BLANK_FILE)
        log.info("cleared power-blank flag at startup")
    except OSError:
        pass

    layout = load_airports()
    strip = make_strip(len(layout))
    cfg = load_config()

    if args.once:
        run_once(strip, layout, cfg, builder=load_builder())
        return

    metars, last_fetch = {}, 0.0
    layout_mtime = None
    while True:
        cfg = load_config()  # web-admin changes apply on the next cycle
        layout, strip, layout_mtime = maybe_reload_layout(
            layout, strip, layout_mtime)
        builder = load_builder()
        if time.time() - last_fetch >= FETCH_INTERVAL:
            fresh = run_once(strip, layout, cfg, builder=builder)
            if fresh:
                metars, last_fetch = fresh, time.time()
            else:
                last_fetch = time.time()  # wait a full interval to retry
        colors, symbols = compute_frame(layout, metars, cfg, builder=builder)
        pct = frame_brightness(cfg, builder, layout)
        if isinstance(strip, SimStrip):
            strip.show(colors, pct, symbols)
        else:
            strip.show(colors, pct)
        time.sleep(RENDER_INTERVAL)


if __name__ == "__main__":
    main()
