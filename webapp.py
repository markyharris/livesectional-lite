#!/usr/bin/env python3
"""webapp.py - LiveSectional Lite web admin.

One page: current status (last fetch, stations reporting), the display
settings form, power buttons, and a map builder that lights one LED at
a time while you edit the airports file. Saving settings writes
config.json; the display loop (metar_lite.py) re-reads it every cycle
and picks up the changes. Builder edits write the airports file and
builder.json, which the display loop also picks up live.

There is deliberately NO login - run this on your LAN only.
"""

import base64
import json
import os
import re
import socket
import subprocess
import threading
import time
from datetime import datetime

from flask import Flask, Response, jsonify, redirect, render_template_string, request, url_for

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "config.json")
STATUS_FILE = os.path.join(HERE, "status.json")
AIRPORTS_FILE = os.path.join(HERE, "airports")
BUILDER_FILE = os.path.join(HERE, "builder.json")

DEFAULT_CONFIG = {
    "map_name": "",
    "brightness": 60,
    "night_mode": True,
    "night_start": "22:00",
    "night_end": "06:00",
    "night_brightness": 25,
    "high_wind": True,
    "lightning": True,
    "map_on": True,
}

app = Flask(__name__)

PAGE = """<!doctype html>
<title>{% if cfg.map_name %}{{ cfg.map_name }} - {% endif %}LiveSectional Lite - Admin</title>
<link rel="icon" type="image/png" href="data:image/png;base64,{{ favicon_b64 }}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: sans-serif; max-width: 34em; margin: 2em auto; padding: 0 1em; }
  label { display: block; margin: 1em 0 0.3em; font-weight: bold; }
  input[type=range] { width: 100%; }
  .row { display: flex; gap: 1em; } .row div { flex: 1; }
  button { margin-top: 1.5em; padding: 0.6em 1.5em; font-size: 1em; }
  .status { background: #f0f0f0; padding: 0.8em 1em; border-radius: 6px; }
  .saved { color: green; }
  hr { border: none; border-top: 1px solid #ccc; margin: 1.8em 0 0; }
</style>
<div style="display:flex;align-items:center;gap:0.6em;margin-bottom:0.8em">
  <img src="data:image/png;base64,{{ favicon_b64 }}" alt="" width="52" height="52"
       style="border-radius:8px">
  <h1 style="margin:0">LiveSectional Lite{% if cfg.map_name %} &mdash; {{ cfg.map_name }}{% endif %}</h1>
</div>
<div class="status">
  <p>Map is <strong id="st-map">{{ "ON" if cfg.map_on else "OFF" }}</strong></p>
  <p>Last fetch: <strong id="st-fetch">{{ status.last_fetch_pretty or status.last_fetch or "never" }}</strong></p>
  <p>Stations reporting: <strong id="st-reporting">{{ status.stations_reporting }}</strong>
     of <span id="st-total">{{ status.stations_total }}</span> mapped</p>
  <p>On the map:
     <span title="VFR" style="color:#00b050">&#9679;</span> <span id="st-vfr">{{ status.counts.VFR }}</span>
     <span title="MVFR" style="color:#0066ff">&#9679;</span> <span id="st-mvfr">{{ status.counts.MVFR }}</span>
     <span title="IFR" style="color:#ff0000">&#9679;</span> <span id="st-ifr">{{ status.counts.IFR }}</span>
     <span title="LIFR" style="color:#e600e6">&#9679;</span> <span id="st-lifr">{{ status.counts.LIFR }}</span></p>
  <p>Pi: <strong>{{ host_name }}</strong>{% if host_ip %} &middot; {{ host_ip }}{% endif %}</p>
  <p id="st-update" style="{% if not update_behind %}display:none;{% endif %}color:#8a6d00;background:#fff3cd;padding:0.4em 0.6em;border-radius:4px">Update available: <strong id="st-behind">{{ update_behind }}</strong> new commit(s) on GitHub. To update: git pull, then restart the web service.</p>
  <p style="color:#555;margin-bottom:0">This status box updates itself
     every 30 seconds - no need to refresh the page.</p>
</div>
<form method="post">
  <input type="hidden" name="toggle_map" value="1">
  <button type="submit" style="margin-top:0.5em">
    {{ "Turn map OFF" if cfg.map_on else "Turn map ON" }}</button>
</form>
<hr>
<h2 style="margin-top:1em">Map builder</h2>
<p style="color:#555;margin:0.2em 0 0">Light one LED on the string, look at
  where it sits on the map, and type the airport that belongs there - no more
  counting LEDs by hand. While the builder light is on, the weather display
  pauses on that one LED (data keeps updating underneath).</p>
{% if builder_error %}<p style="color:#b00">{{ builder_error }}</p>{% endif %}
<div class="status" style="margin-top:0.8em">
  <p>Builder light: <strong>{{ "ON" if builder.active else "OFF" }}</strong></p>
  <p>Position: <strong>LED {{ builder.position }}</strong>
     (line {{ builder.position + 1 }} of {{ airports_count }} in the airports file)</p>
  <p>Current entry: <strong>{{ current_id }}</strong></p>
  {% if neighbors %}
  <p style="color:#555">{% for n in neighbors %}LED {{ n.pos }}: {{ n.id }}{% if not loop.last %} &middot; {% endif %}{% endfor %}</p>
  {% endif %}
</div>
<form method="post">
  <input type="hidden" name="builder_toggle" value="1">
  <button type="submit" style="margin-top:0.5em">
    {{ "Turn builder light OFF" if builder.active else "Light this LED" }}</button>
</form>
<form method="post">
  <div class="row" style="align-items:end">
    <div><label for="builder_pos" style="margin-top:0.5em">Go to LED</label>
      <input type="number" id="builder_pos" name="builder_pos"
             min="0" max="{{ airports_count - 1 }}" value="{{ builder.position }}"></div>
    <div><button type="submit" name="builder_goto" value="1" style="margin-top:0">Go</button></div>
  </div>
  <button type="submit" name="builder_step" value="-1" style="margin-top:0.5em">&larr; Previous</button>
  <button type="submit" name="builder_step" value="1" style="margin-top:0.5em">Next &rarr;</button>
  <p style="margin:0.2em 0 0;color:#555">Stepping or jumping lights the new LED automatically.</p>
</form>
<form method="post">
  <label for="builder_id">Airport at LED {{ builder.position }}</label>
  <input type="text" id="builder_id" name="builder_id" value="{{ current_id }}"
         size="8" style="text-transform:uppercase" autocomplete="off">
  <button type="submit" name="builder_save" value="1" style="margin-top:0.5em">Save airport</button>
  <p style="margin:0.2em 0 0;color:#555">Station ID as it appears in the METAR
     data (e.g. KFLG), or NULL for an unused position, LGND for a legend LED.
     Saving takes effect on the map right away - no restart needed.</p>
</form>
{% if saved %}<p class="saved">Settings saved.</p>{% endif %}
{% if power_error %}<p style="color:#b00">{{ power_error }}</p>{% endif %}
<hr>
<h2 style="margin-top:1em">Power</h2>
<div class="row">
  <form method="post" onsubmit="return confirm('Reboot the Pi now? The map will be back in about a minute.')">
    <input type="hidden" name="power" value="reboot">
    <button type="submit" style="margin-top:0">Reboot</button>
  </form>
  <form method="post" onsubmit="return confirm('Shut the Pi down completely? It will stay off until power is cycled by hand.')">
    <input type="hidden" name="power" value="shutdown">
    <button type="submit" style="margin-top:0">Shutdown</button>
  </form>
</div>
<hr>
<h2 style="margin-top:1em">Settings</h2>
<form method="post">
  <label for="map_name">Map name (optional)</label>
  <input type="text" id="map_name" name="map_name" value="{{ cfg.map_name }}"
         size="24" maxlength="40" autocomplete="off">
  <p style="margin:0.2em 0 0;color:#555">Shown in the header and the browser
     tab, so several maps stay easy to tell apart (e.g. Hangar, Office).
     The map's IP address is in the status box above.</p>
  <label for="brightness">Brightness: <span id="bval">{{ cfg.brightness }}</span>%</label>
  <input type="range" id="brightness" name="brightness" min="0" max="100"
         value="{{ cfg.brightness }}"
         oninput="document.getElementById('bval').textContent = this.value">
  <label><input type="checkbox" name="night_mode" {% if cfg.night_mode %}checked{% endif %}>
    Night mode (dim overnight)</label>
  <div class="row">
    <div><label for="night_start">Night start</label>
      <input type="time" id="night_start" name="night_start" value="{{ cfg.night_start }}"></div>
    <div><label for="night_end">Night end</label>
      <input type="time" id="night_end" name="night_end" value="{{ cfg.night_end }}"></div>
  </div>
  <label for="night_brightness">Night brightness: <span id="nbval">{{ cfg.night_brightness }}</span>% of normal</label>
  <input type="range" id="night_brightness" name="night_brightness" min="0" max="100"
         value="{{ cfg.night_brightness }}"
         oninput="document.getElementById('nbval').textContent = this.value">
  <p style="margin:0.2em 0 0;color:#555">Applies while night mode is active.
     Set to 0% to turn the map completely off overnight.</p>
  <label><input type="checkbox" name="high_wind" {% if cfg.high_wind %}checked{% endif %}>
    High-wind blink (wind or gust &ge; 15 kt)</label>
  <label><input type="checkbox" name="lightning" {% if cfg.lightning %}checked{% endif %}>
    Lightning flash (thunderstorm in the METAR)</label>
  <button type="submit">Save settings</button>
</form>
<p style="color:#888;font-size:0.85em;margin-top:1.8em">Weather data:
NOAA Aviation Weather Center, refreshed every 5 minutes. Displayed
conditions are indicative only - not for navigation.</p>
<script>
// Keep the status box current without a page refresh: the display loop
// fetches every 5 minutes, so poll the server every 30 seconds and swap
// in the new values. Only the status box changes - form input elsewhere
// on the page is never touched.
(function () {
  function setText(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
  }
  function updateStatus() {
    fetch("/api/status").then(function (r) { return r.json(); })
      .then(function (s) {
        setText("st-map", s.map_on ? "ON" : "OFF");
        setText("st-fetch", s.last_fetch_pretty);
        setText("st-reporting", s.stations_reporting);
        setText("st-total", s.stations_total);
        setText("st-vfr", s.counts.VFR);
        setText("st-mvfr", s.counts.MVFR);
        setText("st-ifr", s.counts.IFR);
        setText("st-lifr", s.counts.LIFR);
        var upd = document.getElementById("st-update");
        if (upd) {
          if (s.update_available) {
            setText("st-behind", s.commits_behind);
            upd.style.display = "";
          } else {
            upd.style.display = "none";
          }
        }
      }).catch(function () { /* keep the values already on screen */ });
  }
  setInterval(updateStatus, 30000);
})();
</script>
"""


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def host_info():
    """This Pi's hostname and LAN address, shown in the status box so a
    map can be found on the network without any configuration. The
    address comes from a UDP 'connect' to a public IP - no packet is
    sent; the socket just reveals which local address would be used."""
    try:
        name = socket.gethostname()
    except OSError:
        name = "unknown"
    ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        try:
            ip = socket.gethostbyname(name)
        except OSError:
            ip = ""
    return name, ip


def _ordinal(day):
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def format_last_fetch(value):
    """Human-readable fetch time, e.g. 'Saturday Oct 24th at 9:55 am'.

    status.json stores an ISO timestamp; anything unparseable falls back
    to the raw value so the page never breaks over a format change."""
    if not value:
        return "never"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    hour = dt.strftime("%I").lstrip("0") or "12"
    return (f"{dt.strftime('%A %b')} {_ordinal(dt.day)} at "
            f"{hour}:{dt.strftime('%M')} {dt.strftime('%p').lower()}")


def load_status():
    try:
        with open(STATUS_FILE) as f:
            status = json.load(f)
    except (OSError, ValueError):
        status = {"last_fetch": None, "stations_reporting": 0, "stations_total": 0}
    status["last_fetch_pretty"] = format_last_fetch(status.get("last_fetch"))
    # Category counts (older status files may not have them yet)
    counts = {"VFR": 0, "MVFR": 0, "IFR": 0, "LIFR": 0}
    if isinstance(status.get("counts"), dict):
        for k in counts:
            try:
                counts[k] = int(status["counts"].get(k, 0))
            except (TypeError, ValueError):
                pass
    status["counts"] = counts
    return status


# ---------------------------------------------------------------------------
# Update check
#
# The install is a git clone, so "is an update available?" is just "is
# origin/main ahead of us?" A background thread runs git fetch at
# startup and then hourly (plain git - no GitHub API, no rate limits)
# and counts the commits we are behind. The result only ever surfaces
# as the amber line in the status box; any failure (no network, not a
# git checkout, git missing) leaves the count at zero and the page
# stays quiet.
# ---------------------------------------------------------------------------
UPDATE_STATE = {"behind": 0}


def _git(*args):
    return subprocess.run(
        ["git", "-C", HERE, *args], capture_output=True, text=True,
        timeout=30, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def check_for_update():
    try:
        if _git("fetch", "--quiet", "origin").returncode != 0:
            return
        out = _git("rev-list", "--count", "HEAD..origin/main")
        if out.returncode == 0:
            UPDATE_STATE["behind"] = int(out.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass


def _update_watcher():
    while True:
        check_for_update()
        time.sleep(3600)


def valid_time(s, fallback):
    return s if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s or "") else fallback


# ---------------------------------------------------------------------------
# Map builder
#
# The builder edits the airports file (one entry per LED position, in
# string order) and asks the display process to light the position being
# edited. The handoff is builder.json: this app writes it, metar_lite.py
# reads it every render cycle and lights that one LED. Only the display
# process ever drives the strip - the web app never touches the LEDs.
# ---------------------------------------------------------------------------
def load_airports_list():
    """Airport entries in LED order (same rule as the display: non-blank
    lines, line 1 = LED 0; NULL and LGND occupy real positions)."""
    try:
        with open(AIRPORTS_FILE) as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []


def save_airports_list(entries):
    """Write the airports file atomically (temp file + rename) so the
    display process never reads a half-written file."""
    tmp = AIRPORTS_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(entries) + "\n")
    os.replace(tmp, AIRPORTS_FILE)


def load_builder(count):
    state = {"position": 0, "active": False}
    try:
        with open(BUILDER_FILE) as f:
            saved = json.load(f)
        state["position"] = int(saved.get("position", 0))
        state["active"] = bool(saved.get("active", False))
    except (OSError, ValueError, TypeError):
        pass
    if count:
        state["position"] = max(0, min(count - 1, state["position"]))
    return state


def save_builder(state):
    try:
        with open(BUILDER_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def valid_airport_id(s):
    """Station IDs as they appear in the METAR cache (3-5 characters),
    plus the two layout keywords."""
    return s in ("NULL", "LGND") or \
        re.fullmatch(r"[A-Z0-9]{3,5}", s or "") is not None


def builder_context():
    """Template context for the builder section: current position, the
    entry there, and a few neighbors on either side for orientation."""
    entries = load_airports_list()
    b = load_builder(len(entries))
    pos = b["position"]
    neighbors = [{"pos": p, "id": entries[p]}
                 for p in range(max(0, pos - 2), min(len(entries), pos + 3))]
    return {
        "builder": b,
        "airports_count": len(entries),
        "current_id": entries[pos] if entries else "(no airports file)",
        "neighbors": neighbors,
    }


# ---------------------------------------------------------------------------
# Power actions (Reboot / Shutdown buttons)
#
# The web service normally runs unprivileged, so power actions go through
# passwordless sudo limited to exactly these two commands. One-time setup
# on the Pi (as the user the web service runs as):
#   echo '<user> ALL=(root) NOPASSWD: /sbin/reboot, /sbin/poweroff' \
#       | sudo tee /etc/sudoers.d/livesectional-lite-power
#   sudo chmod 440 /etc/sudoers.d/livesectional-lite-power
# If the web service runs as root, sudo succeeds without that file.
# ---------------------------------------------------------------------------
def _sudo_ready():
    """True if passwordless sudo is available (never prompts)."""
    try:
        return subprocess.run(["/usr/bin/sudo", "-n", "/bin/true"],
                              capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _do_power(kind):
    cmd = "/sbin/reboot" if kind == "reboot" else "/sbin/poweroff"
    try:
        subprocess.run(["/usr/bin/sudo", "-n", cmd],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


# The display process watches for this flag file and blanks the strip
# while it exists (it clears the flag itself at startup, so a reboot
# comes back lit; a shutdown leaves the strip dark instead of frozen
# on its last frame). Same folder as metar_lite.py by construction.
POWER_BLANK_FILE = os.path.join(HERE, "leds_off.flag")


def _blank_leds():
    try:
        with open(POWER_BLANK_FILE, "w") as f:
            f.write("power action in progress\n")
    except OSError:
        pass


POWER_PAGE = """<!doctype html>
<title>LiveSectional Lite - {{ kind }}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<body style="font-family:sans-serif;max-width:34em;margin:2em auto;padding:0 1em">
<h1>{{ heading }}</h1>
<p>{{ detail }}</p>
</body>"""


@app.route("/api/status")
def api_status():
    """JSON form of the status box, polled by the page every 30 seconds
    so Last fetch (and the counts) stay current without a refresh."""
    status = load_status()
    return jsonify({
        "last_fetch": status.get("last_fetch"),
        "last_fetch_pretty": status.get("last_fetch_pretty") or "never",
        "stations_reporting": status.get("stations_reporting", 0),
        "stations_total": status.get("stations_total", 0),
        "counts": status["counts"],
        "map_on": bool(load_config().get("map_on", True)),
        "update_available": UPDATE_STATE["behind"] > 0,
        "commits_behind": UPDATE_STATE["behind"],
    })


# Tab icon: a scrap of sectional chart with a green (VFR) LED lit at the
# airport. Embedded as base64 so the app stays a single file; the same
# bytes are also served at /favicon.ico for browsers that ask there.
FAVICON_B64 = "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"


@app.route("/favicon.ico")
def favicon():
    return Response(base64.b64decode(FAVICON_B64), mimetype="image/png")


def render_page(cfg, saved=False, power_error=None, builder_error=None):
    host_name, host_ip = host_info()
    return render_template_string(
        PAGE, cfg=cfg, status=load_status(), saved=saved,
        favicon_b64=FAVICON_B64, host_name=host_name, host_ip=host_ip,
        update_behind=UPDATE_STATE["behind"],
        power_error=power_error, builder_error=builder_error,
        **builder_context())


@app.route("/", methods=["GET", "POST"])
def index():
    cfg = load_config()
    if request.method == "POST":
        # Map-builder actions first: those forms carry only builder
        # fields, and each ends in a redirect so a refresh can't repeat it.
        if any(k in request.form for k in
               ("builder_toggle", "builder_step", "builder_goto",
                "builder_save")):
            entries = load_airports_list()
            b = load_builder(len(entries))
            if "builder_toggle" in request.form:
                b["active"] = not b["active"]
            elif "builder_step" in request.form:
                try:
                    step = int(request.form["builder_step"])
                except ValueError:
                    step = 0
                if entries:
                    b["position"] = max(0, min(len(entries) - 1,
                                               b["position"] + step))
                    b["active"] = True  # stepping lights the new LED
            elif "builder_goto" in request.form:
                try:
                    pos = int(request.form.get("builder_pos",
                                               b["position"]))
                except ValueError:
                    pos = b["position"]
                if entries:
                    b["position"] = max(0, min(len(entries) - 1, pos))
                    b["active"] = True
            elif "builder_save" in request.form:
                new_id = (request.form.get("builder_id")
                          or "").strip().upper()
                if not entries or not valid_airport_id(new_id):
                    return render_page(
                        cfg, builder_error="That doesn't look like a "
                        "station ID. Use the ID as it appears in the "
                        "METAR data (e.g. KFLG), or NULL / LGND.")
                entries[b["position"]] = new_id
                save_airports_list(entries)
                b["active"] = True  # keep the light on the saved position
            save_builder(b)
            return redirect(url_for("index"))
        kind = request.form.get("power")
        if kind in ("reboot", "shutdown"):
            if not _sudo_ready():
                return render_page(
                    cfg, power_error="Power commands aren't permitted "
                    "for the web service user yet — see the one-time "
                    "sudoers setup in README.md (systemd section), "
                    "then try again.")
            _blank_leds()
            # Delay so this response reaches the browser before the
            # system starts going down.
            threading.Timer(1.5, _do_power, args=(kind,)).start()
            if kind == "reboot":
                heading, detail = ("Rebooting…",
                                   "The Pi is restarting. The map should "
                                   "be back in about a minute; reload "
                                   "this page then.")
            else:
                heading, detail = ("Shutting down…",
                                   "The Pi is powering off. It will stay "
                                   "off until power is cycled by hand.")
            return render_template_string(POWER_PAGE, kind=kind,
                                          heading=heading, detail=detail)
        if "toggle_map" in request.form:
            cfg["map_on"] = not cfg.get("map_on", True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f, indent=2)
            return redirect(url_for("index"))
        try:
            brightness = int(request.form.get("brightness", cfg["brightness"]))
        except ValueError:
            brightness = cfg["brightness"]
        try:
            night_brightness = int(request.form.get(
                "night_brightness", cfg.get("night_brightness", 25)))
        except ValueError:
            night_brightness = cfg.get("night_brightness", 25)
        cfg.update({
            "map_name": (request.form.get("map_name") or "").strip()[:40],
            "brightness": max(0, min(100, brightness)),
            "night_mode": "night_mode" in request.form,
            "night_start": valid_time(request.form.get("night_start"), cfg["night_start"]),
            "night_end": valid_time(request.form.get("night_end"), cfg["night_end"]),
            "night_brightness": max(0, min(100, night_brightness)),
            "high_wind": "high_wind" in request.form,
            "lightning": "lightning" in request.form,
        })
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
        return redirect(url_for("index", saved=1))
    return render_page(cfg, saved=request.args.get("saved"))


if __name__ == "__main__":
    # LAN only - no authentication by design.
    threading.Thread(target=_update_watcher, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
