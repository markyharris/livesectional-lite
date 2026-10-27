#!/usr/bin/env python3
"""webapp.py - LiveSectional Lite web admin.

One page: current status (last fetch, stations reporting), the display
settings form, power buttons, a station list (category color per
airport, raw METAR on hover), and a map builder that lights one LED at
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
    "use_wipe": True,
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
  details.section { margin: 0.2em 0; }
  summary { cursor: pointer; }
  .sta-grid { display: flex; flex-wrap: wrap; gap: 0.4em 1.1em; margin-top: 0.6em; }
  .sta .dot { display: inline-block; width: 0.72em; height: 0.72em;
              border-radius: 50%; margin-right: 0.35em; vertical-align: -0.05em; }
  .sta-id { position: relative; border-bottom: 1px dotted #999;
            cursor: pointer; }
  .sta-id .tip { display: none; position: absolute; left: 0; bottom: 1.5em;
                 z-index: 10; background: #222; color: #eee;
                 padding: 0.5em 0.7em; border-radius: 6px;
                 font-family: monospace; font-size: 0.85em;
                 white-space: normal; width: max-content;
                 max-width: min(24em, 80vw);
                 box-shadow: 0 2px 8px rgba(0,0,0,0.35); }
  .sta-id.open .tip { display: block; }
  #sta-filter { padding: 0.35em 0.5em; font-size: 1em; width: 12em;
                max-width: 100%; }
  @media (hover: hover) { .sta-id:hover .tip { display: block; } }
  @media (max-width: 600px) {
    .sta-id .tip { position: fixed; left: 1em; right: 1em; top: 4em;
                   bottom: auto; width: auto; max-width: none; }
  }
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
     <span title="VFR" style="color:#00b050;font-size:1.6em;vertical-align:-0.12em">&#9679;</span> <span id="st-vfr">{{ status.counts.VFR }}</span>
     <span title="MVFR" style="color:#0066ff;font-size:1.6em;vertical-align:-0.12em">&#9679;</span> <span id="st-mvfr">{{ status.counts.MVFR }}</span>
     <span title="IFR" style="color:#ff0000;font-size:1.6em;vertical-align:-0.12em">&#9679;</span> <span id="st-ifr">{{ status.counts.IFR }}</span>
     <span title="LIFR" style="color:#e600e6;font-size:1.6em;vertical-align:-0.12em">&#9679;</span> <span id="st-lifr">{{ status.counts.LIFR }}</span></p>
  <p>Pi: <strong>{{ host_name }}</strong>{% if host_ip %} &middot; {{ host_ip }}{% endif %}</p>
  <div id="st-update" style="{% if not update_behind and not update_error and not update_failed %}display:none;{% endif %}color:#8a6d00;background:#fff3cd;padding:0.4em 0.6em;border-radius:4px">
  <p style="margin:0.2em 0">Update available: <strong id="st-behind">{{ update_behind }}</strong> new commit(s) on GitHub.</p>
  <form method="post" onsubmit="return confirm('Update now? The Pi will pull the new code and restart both map services; this page reloads itself when the update is done.')">
    <input type="hidden" name="update_apply" value="1">
    <button type="submit" style="margin:0.2em 0">Update now</button>
  </form>
  {% if update_error %}<p style="color:#b00;margin:0.3em 0">{{ update_error }}</p>{% endif %}
  {% if update_failed %}<p style="color:#b00;margin:0.3em 0">The last update attempt failed: {{ update_failed }}</p>{% endif %}
  <p style="margin:0.3em 0 0.1em">Or update by hand:<br>
  <code>{{ update_cmd_pull }}</code><br>
  <code>{{ update_cmd_restart }}</code></p>
  </div>
  <p style="color:#555;margin-bottom:0">This status box updates itself
     every 30 seconds - no need to refresh the page.</p>
  {% if saved %}<p class="saved" style="margin-bottom:0">Settings saved.</p>{% endif %}
</div>
<hr>
<details class="section" id="sec-builder"{% if builder_error or builder.active %} open{% endif %}>
<summary style="margin-top:1em"><h2 style="display:inline;margin:0">Map Builder</h2></summary>
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
     data (e.g. KFLG), or NULL for an unused position, LGND for a legend LED
     (a tagged legend like LGND:WIND pins that LED's color - the buttons
     below set those for you). Saving takes effect on the map right
     away - no restart needed.</p>
</form>
<form method="post">
  <p style="margin:0.6em 0 0;color:#555">Or make this LED a legend position
     with a fixed color, so it always matches your printed legend:</p>
  <button type="submit" name="builder_legend" value="VFR" style="margin-top:0.3em">VFR</button>
  <button type="submit" name="builder_legend" value="MVFR" style="margin-top:0.3em">MVFR</button>
  <button type="submit" name="builder_legend" value="IFR" style="margin-top:0.3em">IFR</button>
  <button type="submit" name="builder_legend" value="LIFR" style="margin-top:0.3em">LIFR</button>
  <button type="submit" name="builder_legend" value="NODATA" style="margin-top:0.3em">No data</button>
  <button type="submit" name="builder_legend" value="WIND" style="margin-top:0.3em">Wind</button>
  <button type="submit" name="builder_legend" value="TS" style="margin-top:0.3em">Storms</button>
  <button type="submit" name="builder_legend" value="" style="margin-top:0.3em">Plain LGND</button>
</form>
{% if uploaded_count %}<p class="saved">Airports file uploaded -
  {{ uploaded_count }} positions loaded.</p>{% endif %}
<form method="post" enctype="multipart/form-data"
      onsubmit="return confirm('Replace the whole airports list with the uploaded file? Your current list is kept as airports.bak.')">
  <label for="airports_file">Upload an airports file</label>
  <input type="file" id="airports_file" name="airports_file" accept=".txt,text/plain">
  <button type="submit" name="builder_upload" value="1" style="margin-top:0.5em">Upload and replace list</button>
  <p style="margin:0.2em 0 0;color:#555">Already have a list - for example
     the airports file from an original LiveSectional? Its format is the
     same (one station ID, NULL or LGND per line, in LED order), so it
     uploads as-is. The upload replaces the whole list and the map picks
     it up on its next update - no restart needed. Your previous list is
     kept as airports.bak.</p>
</form>
</details>
<hr>
<details class="section" id="sec-power"{% if power_error %} open{% endif %}>
<summary style="margin-top:1em"><h2 style="display:inline;margin:0">Power</h2></summary>
{% if power_error %}<p style="color:#b00">{{ power_error }}</p>{% endif %}
<div class="row">
  <form method="post">
    <input type="hidden" name="toggle_map" value="1">
    <button type="submit" style="margin-top:0">
      {{ "Turn Off LED's" if cfg.map_on else "Turn On LED's" }}</button>
  </form>
  <form method="post" onsubmit="return confirm('Reboot the Pi now? The map will be back in about a minute.')">
    <input type="hidden" name="power" value="reboot">
    <button type="submit" style="margin-top:0">Reboot</button>
  </form>
  <form method="post" onsubmit="return confirm('Shut the Pi down completely? It will stay off until power is cycled by hand.')">
    <input type="hidden" name="power" value="shutdown">
    <button type="submit" style="margin-top:0">Shutdown</button>
  </form>
</div>
</details>
<hr>
<details class="section" id="sec-settings">
<summary style="margin-top:1em"><h2 style="display:inline;margin:0">Settings</h2></summary>
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
  <label><input type="checkbox" name="use_wipe" {% if cfg.use_wipe %}checked{% endif %}>
    Use wipe (each update blanks the string briefly, then sweeps in the new data)</label>
  <button type="submit">Save settings</button>
</form>
</details>
<hr>
<details class="section" id="sec-stations">
<summary style="margin-top:1em"><h2 style="display:inline;margin:0">Stations</h2></summary>
<p style="color:#555;margin:0.2em 0 0">Every airport on the map with its
  current flight-category color. Hover or tap an airport ID to see its
  raw METAR. The list refreshes along with the status box, every 30
  seconds.</p>
{% if stations %}
<p style="margin:0.6em 0 0"><input type="search" id="sta-filter"
  placeholder="Filter by airport ID" autocomplete="off">
  <span id="sta-count" style="color:#555">{{ stations|length }} airports</span></p>
<div class="sta-grid">
{% for s in stations %}
  <span class="sta" data-sid="{{ s.id }}"><span class="dot" title="{{ s.category }}" style="background:{{ s.hex }}"></span><span class="sta-id">{{ s.id }}<span class="tip">{{ s.tip }}</span></span></span>
{% endfor %}
</div>
<p id="sta-nomatch" style="display:none;color:#555">No airports match that filter.</p>
{% else %}
<p style="color:#555">No airports file found yet.</p>
{% endif %}
</details>
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
  var CAT_HEX = { VFR: "#00b050", MVFR: "#0066ff", IFR: "#ff0000",
                  LIFR: "#e600e6" };
  function updateStations(stations) {
    document.querySelectorAll(".sta").forEach(function (row) {
      var info = stations ? stations[row.getAttribute("data-sid")] : null;
      var dot = row.querySelector(".dot");
      var tip = row.querySelector(".tip");
      if (info && CAT_HEX[info.category]) {
        if (dot) { dot.style.background = CAT_HEX[info.category];
                   dot.title = info.category; }
        if (tip) tip.textContent = info.raw ||
            "No METAR text in the latest report.";
      } else {
        if (dot) { dot.style.background = "#c0c0c0"; dot.title = "No data"; }
        if (tip) tip.textContent = "No METAR received in the latest fetch.";
      }
    });
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
        updateStations(s.stations);
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
<script>
// Station METAR popups: on a mouse, hovering an airport ID shows its
// popup (CSS). Touch screens have no real hover, so a tap toggles the
// popup instead - tapping another airport switches to it, tapping
// anywhere else closes it.
document.querySelectorAll(".sta-id").forEach(function (el) {
  el.addEventListener("click", function (ev) {
    ev.stopPropagation();
    var wasOpen = el.classList.contains("open");
    document.querySelectorAll(".sta-id.open").forEach(function (o) {
      o.classList.remove("open");
    });
    if (!wasOpen) el.classList.add("open");
  });
});
document.addEventListener("click", function () {
  document.querySelectorAll(".sta-id.open").forEach(function (o) {
    o.classList.remove("open");
  });
});
</script>
<script>
// Stations filter: as you type, narrow the list to airport IDs that
// contain the text (handy on a big map), and keep the count honest.
// Clearing the box brings the whole list back. The 30-second refresh
// only touches dots and popups, so it never fights the filter.
(function () {
  var box = document.getElementById("sta-filter");
  if (!box) return;
  var rows = document.querySelectorAll(".sta");
  var count = document.getElementById("sta-count");
  var nomatch = document.getElementById("sta-nomatch");
  box.addEventListener("input", function () {
    var q = box.value.trim().toUpperCase();
    var shown = 0;
    rows.forEach(function (row) {
      var hit = !q || row.getAttribute("data-sid").indexOf(q) !== -1;
      row.style.display = hit ? "" : "none";
      if (hit) shown++;
    });
    if (count) count.textContent = q
      ? shown + " of " + rows.length + " airports"
      : rows.length + " airports";
    if (nomatch) nomatch.style.display = shown ? "none" : "";
  });
})();
</script>
<script>
// Collapsible sections: remember which ones this browser left open,
// so a page reload - every button here posts and reloads - doesn't
// slam the section shut mid-task. Sections the server rendered open
// (an error to show, the builder light on) stay open regardless.
document.querySelectorAll("details.section").forEach(function (d) {
  try {
    if (localStorage.getItem("lsl-open-" + d.id) === "1") d.open = true;
  } catch (e) { /* storage unavailable - sections just start closed */ }
  d.addEventListener("toggle", function () {
    try {
      localStorage.setItem("lsl-open-" + d.id, d.open ? "1" : "0");
    } catch (e) {}
  });
});
</script>
<script>
// Map builder scroll-back: builder actions post and reload, which
// would otherwise drop the user back at the top of the page, hunting
// for the builder section on a small screen. Remember the scroll
// position when a builder form is submitted and restore it on the
// reload (one-shot - a plain manual refresh stays at the top).
(function () {
  var sec = document.getElementById("sec-builder");
  if (!sec) return;
  sec.querySelectorAll("form").forEach(function (f) {
    f.addEventListener("submit", function () {
      try {
        sessionStorage.setItem("lsl-scroll", String(window.scrollY));
      } catch (e) {}
    });
  });
  try {
    var y = sessionStorage.getItem("lsl-scroll");
    if (y !== null) {
      sessionStorage.removeItem("lsl-scroll");
      window.scrollTo(0, parseInt(y, 10) || 0);
    }
  } catch (e) {}
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
    # Per-station detail (older status files may not have it yet)
    stations = {}
    if isinstance(status.get("stations"), dict):
        for sid, info in status["stations"].items():
            if isinstance(info, dict):
                stations[str(sid)] = {
                    "category": str(info.get("category") or ""),
                    "raw": str(info.get("raw") or ""),
                }
    status["stations"] = stations
    return status


CATEGORY_HEX = {"VFR": "#00b050", "MVFR": "#0066ff", "IFR": "#ff0000",
                "LIFR": "#e600e6"}
NO_DATA_HEX = "#c0c0c0"  # silver, matching the map's no-data LED


def station_rows():
    """Rows for the Stations section: every airport on the map, in
    airports-file order (a station listed twice appears once; NULL and
    LGND positions are not airports), each with its current category
    color and the text for its hover popup - the raw METAR, or a note
    when the station did not report."""
    reporting = load_status()["stations"]
    rows, seen = [], set()
    for entry in load_airports_list():
        if entry in seen or entry == "NULL" or entry.startswith("LGND"):
            continue
        seen.add(entry)
        info = reporting.get(entry)
        if info and info["category"] in CATEGORY_HEX:
            rows.append({"id": entry, "category": info["category"],
                         "hex": CATEGORY_HEX[info["category"]],
                         "tip": info["raw"] or
                                "No METAR text in the latest report."})
        else:
            rows.append({"id": entry, "category": "No data",
                         "hex": NO_DATA_HEX,
                         "tip": "No METAR received in the latest fetch."})
    return rows


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
            if UPDATE_STATE["behind"] == 0 and \
                    UPDATE_RUN["state"] == "failed":
                # Current again (the update landed, or was pulled by
                # hand): the old failure note no longer applies.
                UPDATE_RUN.update(state="idle", detail="")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass


def _update_watcher():
    while True:
        check_for_update()
        time.sleep(3600)


# ---------------------------------------------------------------------------
# One-click update
#
# The "Update now" button in the amber box runs the same two steps the
# box prints for doing it by hand: git pull, then restart both
# services. The pull needs no privilege (the web service user owns the
# files and already runs git for the check above); the restarts go
# through the same passwordless-sudo setup as the power buttons - the
# documented sudoers line already grants systemctl on the map's
# services. The work runs in a background thread after the page has
# answered, display service first and the web service last (restarting
# it ends this process, which is the success path). A failed pull stops
# everything, restarts nothing, and records the reason here for the
# page to show.
# ---------------------------------------------------------------------------
UPDATE_RUN = {"state": "idle", "detail": "", "started": 0.0}


def _restart_permitted():
    """True if passwordless sudo allows restarting both services.
    Probed with `sudo -l`, which reports permission for that exact
    command without running anything."""
    for svc in (_display_service_name(), web_service_name()):
        try:
            r = subprocess.run(["/usr/bin/sudo", "-n", "-l",
                                "/bin/systemctl", "restart", svc],
                               capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if r.returncode != 0:
            return False
    return True


def _do_update():
    try:
        pull = _git("pull", "--ff-only")
        if pull.returncode != 0:
            out = (pull.stderr or pull.stdout or "").strip()
            UPDATE_RUN.update(
                state="failed",
                detail=out.splitlines()[-1] if out else "git pull failed")
            return
        UPDATE_RUN.update(state="restarting", detail="")
        for svc in (_display_service_name(), web_service_name()):
            subprocess.run(["/usr/bin/sudo", "-n", "/bin/systemctl",
                            "restart", svc],
                           capture_output=True, timeout=60)
        UPDATE_RUN.update(state="done", detail="")
    except (OSError, subprocess.TimeoutExpired) as e:
        UPDATE_RUN.update(state="failed", detail=str(e))


UPDATE_PAGE = """<!doctype html>
<title>LiveSectional Lite - Updating</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<body style="font-family:sans-serif;max-width:34em;margin:2em auto;padding:0 1em">
<h1>Updating&hellip;</h1>
<p>The Pi is pulling the new code and restarting the map's services.
This page reloads itself once the admin page answers again; if the
update hit a problem, the page will say what happened.</p>
<p><a href="/">Reload now</a></p>
<script>
setTimeout(function () {
  var iv = setInterval(function () {
    fetch("/api/status").then(function (r) {
      if (r.ok) { clearInterval(iv); location.href = "/"; }
    }).catch(function () { /* not back yet - keep waiting */ });
  }, 5000);
}, 10000);
</script>
</body>"""


def _service_from_cgroup(text):
    """The systemd unit running this process ends the cgroup path
    (e.g. /system.slice/metar-lite-web.service). Fresh installs use
    the documented livesectional-lite-web name as the fallback."""
    names = re.findall(r"([A-Za-z0-9_.@-]+)\.service", text or "")
    return names[-1] if names else "livesectional-lite-web"


def web_service_name():
    try:
        with open("/proc/self/cgroup") as f:
            return _service_from_cgroup(f.read())
    except OSError:
        return "livesectional-lite-web"


def _display_service_name():
    web = web_service_name()
    return web[:-4] if web.endswith("-web") else web


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


LEGEND_TAGS = ("VFR", "MVFR", "IFR", "LIFR", "NODATA", "WIND", "TS")


def valid_airport_id(s):
    """Station IDs as they appear in the METAR cache (3-5 characters),
    plus the layout keywords: NULL, LGND, and tagged legend entries
    (LGND:VFR, LGND:WIND, ...) that pin a legend LED to its color."""
    if s in ("NULL", "LGND"):
        return True
    if (s or "").startswith("LGND:"):
        return s[5:] in LEGEND_TAGS
    return re.fullmatch(r"[A-Z0-9]{3,5}", s or "") is not None


MAX_AIRPORTS = 2500
MAX_UPLOAD_BYTES = 200_000


def parse_airports_upload(upload):
    """Validate an uploaded airports file (a backup from an original
    LiveSectional is the expected case - same format: one entry per
    line, in LED order). Returns (entries, error): the entries
    normalized (trimmed, uppercased, blank lines dropped, any line
    endings accepted), or None and a plain-language error."""
    if upload is None or not upload.filename:
        return None, "Choose an airports file to upload first."
    raw = upload.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        return None, "That file is too large to be an airports file."
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, ("That file doesn't look like plain text. Upload "
                      "the airports file itself - one station per line.")
    numbered = [(n, line.strip().upper())
                for n, line in enumerate(text.splitlines(), 1)
                if line.strip()]
    entries = [entry for _, entry in numbered]
    if not entries:
        return None, "No airport entries found in that file."
    if len(entries) > MAX_AIRPORTS:
        return None, (f"That file lists {len(entries)} positions - "
                      f"the limit here is {MAX_AIRPORTS}.")
    bad = [(n, e) for n, e in numbered if not valid_airport_id(e)][:3]
    if bad:
        shown = ", ".join(f"line {n} ('{e}')" for n, e in bad)
        return None, (f"Some entries don't look like station IDs: "
                      f"{shown}. Use the ID as it appears in the "
                      f"METAR data, or NULL / LGND.")
    return entries, None


def backup_airports_file():
    """Keep the list an upload is about to replace as airports.bak, so
    uploading the wrong file is undoable by copying it back."""
    try:
        if load_airports_list():
            with open(AIRPORTS_FILE, "rb") as src, \
                    open(AIRPORTS_FILE + ".bak", "wb") as dst:
                dst.write(src.read())
    except OSError:
        pass


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
        "stations": status["stations"],
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


def render_page(cfg, saved=False, power_error=None, builder_error=None,
                uploaded_count=0, update_error=None):
    host_name, host_ip = host_info()
    web_svc = web_service_name()
    disp_svc = _display_service_name()
    return render_template_string(
        PAGE, cfg=cfg, status=load_status(), saved=saved,
        favicon_b64=FAVICON_B64, host_name=host_name, host_ip=host_ip,
        update_behind=UPDATE_STATE["behind"],
        update_cmd_pull=f"cd {HERE} && git pull",
        update_cmd_restart=f"sudo systemctl restart {disp_svc} {web_svc}",
        power_error=power_error, builder_error=builder_error,
        uploaded_count=uploaded_count, stations=station_rows(),
        update_error=update_error,
        update_failed=UPDATE_RUN["detail"]
        if UPDATE_RUN["state"] == "failed" else None,
        **builder_context())


@app.route("/", methods=["GET", "POST"])
def index():
    cfg = load_config()
    if request.method == "POST":
        # Map-builder actions first: those forms carry only builder
        # fields, and each ends in a redirect so a refresh can't repeat it.
        if any(k in request.form for k in
               ("builder_toggle", "builder_step", "builder_goto",
                "builder_save", "builder_upload", "builder_legend")):
            entries = load_airports_list()
            b = load_builder(len(entries))
            if "builder_upload" in request.form:
                new_entries, err = parse_airports_upload(
                    request.files.get("airports_file"))
                if err:
                    return render_page(cfg, builder_error=err)
                backup_airports_file()
                save_airports_list(new_entries)
                b["position"] = 0  # the list changed - start at the top
                save_builder(b)
                return redirect(
                    url_for("index", uploaded=len(new_entries)))
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
            elif "builder_legend" in request.form:
                tag = (request.form.get("builder_legend")
                       or "").strip().upper()
                if not entries or (tag and tag not in LEGEND_TAGS):
                    return render_page(
                        cfg, builder_error="That legend choice isn't "
                        "recognized.")
                entries[b["position"]] = "LGND:" + tag if tag else "LGND"
                save_airports_list(entries)
                b["active"] = True  # keep the light on the saved position
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
        if "update_apply" in request.form:
            if UPDATE_STATE["behind"] <= 0:
                return redirect(url_for("index"))
            if UPDATE_RUN["state"] in ("running", "restarting") and \
                    time.time() - UPDATE_RUN["started"] < 600:
                return render_template_string(UPDATE_PAGE)
            if not _restart_permitted():
                return render_page(
                    cfg, update_error="One-click update isn't permitted "
                    "for the web service user yet - the one-time "
                    "sudoers setup in HOWTO.md (Step 7) covers it, or "
                    "run the commands in the box by hand.")
            UPDATE_RUN.update(state="running", detail="",
                              started=time.time())
            # Delay so this response reaches the browser before the
            # pull and the service restarts begin.
            threading.Timer(1.0, _do_update).start()
            return render_template_string(UPDATE_PAGE)
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
            "use_wipe": "use_wipe" in request.form,
        })
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
        return redirect(url_for("index", saved=1))
    try:
        uploaded_count = int(request.args.get("uploaded") or 0)
    except ValueError:
        uploaded_count = 0
    return render_page(cfg, saved=request.args.get("saved"),
                       uploaded_count=uploaded_count)


if __name__ == "__main__":
    # LAN only - no authentication by design.
    threading.Thread(target=_update_watcher, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
