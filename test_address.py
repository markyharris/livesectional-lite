# test_address.py - desktop checks for the ways the map says where
# its admin page lives (standing rule, Nov 16, 2026: a connected
# device tells the owner its admin address).
#
# Part A, the blink code: the code is played through a recording
# strip with the clock faked, and the blink/colour/pause schedule is
# asserted from the recorded LED writes themselves - for .24, .8,
# .108 and .213 - plus the wiring facts (boot path only, never the
# running loop) and the colour path (logical colours in, the strip's
# own scale + GRB wire order out), which is what makes the position
# colours come out true on a real strip.
#
# Part B, the admin page: the real page is rendered with the host
# lookup stubbed, and both of its addresses - the current IP and the
# .local name, read live at serve time - must be on it.
#
# Run from the project root:  python3 test_address.py
# (Part B needs Flask, from requirements.txt; without it those
# checks are skipped, not failed.)
# Exits 0 when every check passes, 1 otherwise.

import inspect
import sys

import metar_lite as ml

FAILED = []


def check(name, cond):
    print("%s %s" % ("PASS" if cond else "FAIL", name))
    if not cond:
        FAILED.append(name)


# ---------------------------------------------------------------------------
# Part A - the blink code, read back off the recorded LED writes
# ---------------------------------------------------------------------------
class RecordingStrip:
    """Stands in for the strip: every show() is recorded verbatim."""

    def __init__(self, count):
        self.count = count
        self.frames = []

    def show(self, colors, pct, symbols=None):
        self.frames.append((list(colors), pct))

    def wipe(self, colors, pct, symbols=None):
        self.frames.append((list(colors), pct))


def _play(ip, count=8):
    """Play the blink code with the clock faked; returns the strip
    and the (color, ms) events reconstructed from the recorded
    frames and sleeps - exactly what the LED was told to do."""
    strip = RecordingStrip(count)
    sleeps = []
    real_sleep = ml.time.sleep
    ml.time.sleep = lambda s: sleeps.append(int(round(s * 1000)))
    try:
        ml.blink_ip(strip, ip, count)
    finally:
        ml.time.sleep = real_sleep
    events = []
    for (frame, _pct), ms in zip(strip.frames, sleeps):
        lit = [c for c in frame if c != ml.OFF]
        events.append((lit[0] if lit else None, ms))
    return strip, events


# The spec, restated independently of the code under test: white,
# amber, violet by digit position; 300 ms blinks; 250 ms between
# blinks of a digit and as the beat after each digit; 900 ms between
# digits; a zero is one 900 ms blink; 2500 ms between repeats; the
# sequence runs three times.
_W, _A, _V = (255, 255, 255), (255, 160, 0), (148, 0, 211)


def _expected(digits):
    ev = []
    for rep in range(3):
        if rep:
            ev.append((None, 2500))
        for pos, d in enumerate(digits):
            if pos:
                ev.append((None, 900))
            color = (_W, _A, _V)[pos]
            if d == 0:
                ev.append((color, 900))
            else:
                for k in range(d):
                    if k:
                        ev.append((None, 250))
                    ev.append((color, 300))
            ev.append((None, 250))
    return ev


for ip, digits in (("192.168.87.24", [2, 4]),
                   ("192.168.87.8", [8]),
                   ("192.168.87.108", [1, 0, 8]),
                   ("192.168.87.213", [2, 1, 3])):
    strip, events = _play(ip)
    check("blink %s: exact blink/colour/pause schedule" % ip,
          events == _expected(digits))
    check("blink %s: only the first LED ever lights, at 60%%" % ip,
          all(pct == 60 and all(c == ml.OFF for c in frame[1:])
              for frame, pct in strip.frames))
    check("blink %s: strip left dark at the end" % ip,
          all(c == ml.OFF for c in strip.frames[-1][0]))

_strip, _events = _play("192.168.87.213")
check("blink .213: the whole sequence takes 20.3 s",
      sum(ms for _c, ms in _events) == 20300)
_strip, _events = _play("192.168.87.255")
check("blink .255 (arithmetic worst case): 30.2 s",
      sum(ms for _c, ms in _events) == 30200)

_silent = RecordingStrip(8)
ml.blink_ip(_silent, "0.0.0.0", 8)
check("blink: 0.0.0.0 (not really connected) stays silent",
      _silent.frames == [])
check("blink: an unparseable address has no schedule",
      ml._blink_schedule("") == [] and ml._blink_schedule("10.0.0") == [])

# The address the code would speak: never loopback, never Comitup's
# setup-AP address (10.41.0.1 is the map mid-rehome, not joined).
_here_ip = ml.local_ip()
check("local_ip: loopback and the Comitup AP address never spoken",
      _here_ip == "" or (not _here_ip.startswith("127.")
                         and _here_ip != "10.41.0.1"))

# Boot joins only: the call lives in main() (the boot path); the
# running loop's render path - where a mid-run reconnect would
# surface - never calls it.
check("blink: wired into main(), the boot path",
      "blink_ip" in inspect.getsource(ml.main)
      and "wait_for_ip" in inspect.getsource(ml.main))
check("blink: never called from the running loop",
      "blink_ip" not in inspect.getsource(ml.run_once)
      and "blink_ip" not in inspect.getsource(ml.compute_frame))

# The position colours must come out true on the wire: blink_ip
# hands the strip logical (R, G, B) tuples (asserted above, off the
# recorded frames), and the strip's own show() path does the rest -
# scale() applies the brightness, and PiStrip drives the library
# configured for GRB wire order.
check("colour path: scale() applies the 60% blink brightness",
      ml.scale((255, 160, 0), 60) == (153, 96, 0)
      and ml.scale((148, 0, 211), 60) == (88, 0, 126))
check("colour path: PiStrip is configured for GRB wire order",
      "WS2811_STRIP_GRB" in inspect.getsource(ml.PiStrip.__init__)
      and "setPixelColorRGB" in inspect.getsource(ml.PiStrip.show))


# ---------------------------------------------------------------------------
# Part B - the admin page names both of its addresses
# ---------------------------------------------------------------------------
try:
    import webapp
except ImportError:
    print("SKIP admin-page checks (flask not installed here)")
    webapp = None

if webapp is not None:
    webapp.host_info = lambda: ("metar-lite", "192.168.87.50")
    _resp = webapp.app.test_client().get("/")
    _html = _resp.get_data(as_text=True)
    check("admin page: serves OK", _resp.status_code == 200)
    check("admin page: shows the full admin URL at the current IP",
          "http://192.168.87.50:5000" in _html)
    check("admin page: shows the full admin URL at the .local name",
          "http://metar-lite.local:5000" in _html)

print()
if FAILED:
    print("%d check(s) FAILED: %s" % (len(FAILED), FAILED))
    sys.exit(1)
print("all checks passed")
