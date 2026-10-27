# LiveSectional Lite — Complete Setup Guide (Raspberry Pi)

Build a sectional-chart LED weather map from scratch: one LED per airport,
colored by the latest METAR (green VFR, blue MVFR, red IFR, magenta LIFR),
refreshed every 5 minutes, with a web page for settings and on/off —
plus Reboot and Shutdown buttons that actually work, and a map builder
that lights one LED at a time so you can enter each airport in the right
position without counting LEDs by hand.

This guide assumes **no Linux experience**. Every command is copy-paste.
Where a command asks for your password, that's normal during setup.

**Copying the commands:** don't copy line by line. Reading this guide
on GitHub, hover over any command block and click the **copy icon** at
its top-right corner — that copies the whole block at once. Paste it
into the terminal in one go (right-click to paste, or Ctrl+Shift+V),
press Enter once, and the lines run in order.

---

## What you need

**Hardware**

- A Raspberry Pi — a **Pi 4 (2 GB)** is ideal. A **Pi Zero 2 W** or
  **3B/3B+** also works well. Even the **original Pi Zero W** runs it —
  field-tested: very slow to boot and to load the admin page, but the
  map lights correctly and everything works (with a Zero W, pick the
  **32-bit** OS in Step 1 — it can't run the 64-bit image). **Avoid the
  Pi 5** — its new I/O chip blocks the LED library this project uses.
- A microSD card, 16 GB or larger.
- The Pi's normal power supply.
- A WS2812B-style LED strip/strand wired to the map.
- A 5 V power supply for the LEDs, sized for your strip. The Pi and the
  LED supply must share a **common ground**.

**Wiring (3 wires)**

| Strip | Connects to |
|---|---|
| 5 V (red) | LED power supply +5 V |
| GND (white/black) | LED supply ground **and** a Pi GND pin |
| Data In (green) | Pi **GPIO 18** (physical pin 12) |

> Data flows one direction: wire to the strip's **input** end (usually
> marked DIN, with arrows pointing away from it along the strip).

**Software files** — five files make the map run. You won't download
them one by one: Step 3 clones the whole project folder from GitHub in
one command (the clone also brings `README.md`, this guide, a second
sample layout `airports-world50`, and a `.gitignore`). The five:

| File | What it is |
|---|---|
| `metar_lite.py` | The display program: fetches weather, drives the LEDs |
| `webapp.py` | The web settings page |
| `config.json` | Settings (the web page edits this for you later) |
| `airports` | Your map layout — one airport per line, in LED order |
| `requirements.txt` | Python package list |

Three more files appear in the folder once things run — they're created
by the programs, **not** downloads, and deleting them is harmless:
`status.json` (the display's latest-fetch report, read by the web page),
`builder.json` (the map builder's current position), and `leds_off.flag`
(a momentary signal used while rebooting/shutting down). The
`.gitignore` in the clone tells Git to leave those three alone.

---

## Three things that trip everyone up (read first)

1. **Your username matters.** When you flash the Pi you choose a username.
   It does *not* have to be `pi` — most aren't. This guide auto-fills your
   real username into every command that needs it, so **don't substitute
   `pi` anywhere**. Most setup failures come from a file path or permission
   naming the wrong user.
2. **The services must point at the real folder.** If the folder path in a
   service file doesn't exist, the service dies instantly with error
   `status=200/CHDIR`. This guide generates the paths from the folder
   you're actually standing in, so they can't be wrong.
3. **`sudo` forgets your password at every reboot — by design.** Its
   memory lasts ~15 minutes and lives in RAM. Step 7 gives the map's own
   commands a permanent password-free pass, which is what makes the
   on-screen power buttons (and painless restarts) possible.

---

## Step 1 — Create the SD card (Raspberry Pi OS Lite)

This writes a fresh, current operating system to the card. **It erases
the card completely** — use a card with nothing on it you need.

1. On your computer, install **Raspberry Pi Imager** from
   raspberrypi.com and put the microSD card in the computer.
2. **Choose Device:** your Pi model.
3. **Choose OS:** select **Raspberry Pi OS (other)**, then
   **Raspberry Pi OS Lite (64-bit)**. Imager downloads the newest Lite
   release each time, so you're current by construction. Lite has no
   desktop — this map is a headless appliance, everything happens in
   the terminal, and Lite boots faster and has less to update.
   (On an **original Pi Zero W**, choose **Raspberry Pi OS Lite
   (32-bit)** instead — that model can't run the 64-bit image.)
4. **Choose Storage:** your SD card (double-check it's the card, not a
   drive you care about).
5. Click **Next**. When Imager offers **OS customisation**, choose
   **Edit Settings** and set:
   - **Hostname:** `raspberrypi` (or anything you like)
   - **Username and password:** pick them, and **remember the username** —
     you'll see it in every terminal prompt. Write the password down.
   - **Wi-Fi:** your network name and password (skip if you'll use
     Ethernet)
   - **Locale:** your **time zone** — the map's night window and the
     admin page's clock depend on it — and your keyboard layout.
   - Under **Services**: **Enable SSH**, with password authentication.
     This lets you run everything below from your computer; without it
     you'll need a keyboard and monitor on the Pi for setup.
6. Save, then **Write** (confirm the erase). When it finishes, eject the
   card, put it in the Pi, and power the Pi on. Wait ~2 minutes for the
   first boot.

Open a terminal: either on the Pi itself, or from your computer with
`ssh yourusername@raspberrypi.local`. Your prompt will look like:

```
yourusername@raspberrypi:~ $
```

Everything below happens in that terminal.

## Step 2 — Update the Pi and install the software libraries

```bash
sudo apt update && sudo apt install -y python3-pip git
sudo pip3 install --break-system-packages flask rpi_ws281x
```

(`git` is the tool Step 3 uses to download the project from GitHub.)

(The `sudo` on the pip line matters: the LED library must be
installed **system-wide**, because the display service runs as root —
installed only for your user, root can't see it and the map silently
falls back to console simulation. The `--break-system-packages` flag
just tells the Pi's newer Python "yes, install system-wide on
purpose." It's fine on a single-purpose appliance like this.)

## Step 3 — Download the project (clone from GitHub)

One command fetches the whole project folder from GitHub:

```bash
git clone https://github.com/markyharris/livesectional-lite.git ~/livesectional-lite
ls ~/livesectional-lite
```

No GitHub account is needed — the repository is public. You should see
nine names: the five program files (`metar_lite.py`, `webapp.py`,
`config.json`, `airports`, `requirements.txt`) plus `README.md`,
`HOWTO.md`, `airports-world50` (a second sample layout), and
`.gitignore`.

Cloning — rather than copying files by hand — is what makes future
updates a single `git pull` command; see "Keeping the Pi in sync with
GitHub" near the end of this guide.

**If `config.json` is missing** from the list, something went wrong
with the download. The map still runs (on built-in defaults) but logs
a "config read failed" warning — fix the download now and save
yourself the confusion later.

*(Working without GitHub? Copying the five program files into
`~/livesectional-lite` by USB stick or `scp` also works — everything
below is the same — but updates will be manual file copies; see
"Updating the programs later.")*

## Step 4 — Set up your `airports` layout file

The `airports` file is the map. Rules:

- **One airport per line, in physical LED order.** Line 1 lights the
  first LED, line 2 the second, and so on.
- Use the station ID exactly as the Aviation Weather Center reports it
  (US stations usually start with K: `KFLG`, `KDEN`). It's case-sensitive.
  The map is worldwide — any reporting station works.
- `NULL` on a line = a spacer: that LED exists but never lights.
- `LGND` lines = legend LEDs (the first four show the category colors).
- Blank lines are ignored. **The number of lines is the LED count** —
  there's nothing else to configure.
- A station that isn't reporting shows as a dark LED. That's the data,
  not a fault.

You don't have to get this file perfect by hand: once the map is
running, the admin page's **Map Builder** (see "Using the admin page")
lights one LED at a time on the physical string so you can walk the
map position by position and type each airport where it belongs.

## Step 5 — Test run (before automating anything)

First, a no-hardware test — it prints one line of letters per update
(`V`/`M`/`I`/`L` for categories, `N` = no weather reported — dim
white on the strip, `.` = off, `G` = legend, `B` = the map builder's
lit position) instead of driving LEDs:

```bash
cd ~/livesectional-lite
python3 metar_lite.py --once
```

You should see a fetch succeed and a line of letters. (Run without
sudo, the test logs "console simulation mode" — that's expected: the
LED hardware itself needs root, so the unprivileged test only prints
the letters.) Now the real thing (`sudo` is required — the LED library
talks directly to the hardware):

```bash
sudo python3 metar_lite.py
```

The strip should light with current conditions within a few seconds and
sweep once after the first fetch. Press **Ctrl+C** to stop. Then start
the web page and visit it:

```bash
python3 webapp.py
```

Open `http://raspberrypi.local:5000` in a browser on the same network
(if that name doesn't resolve, use the Pi's IP address: run
`hostname -I` on the Pi). You should see the status box and settings.
Ctrl+C to stop it when done.

## Step 6 — Start automatically at boot (systemd)

These commands **generate** the service files using your real folder and
username, so there's nothing to edit:

```bash
cd ~/livesectional-lite
APP=$PWD
ME=$(whoami)
echo "Folder: $APP   User: $ME"

sudo tee /etc/systemd/system/livesectional-lite.service > /dev/null <<EOF
[Unit]
Description=LiveSectional Lite display
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$APP
ExecStart=/usr/bin/python3 metar_lite.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/livesectional-lite-web.service > /dev/null <<EOF
[Unit]
Description=LiveSectional Lite web admin
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$APP
ExecStart=/usr/bin/python3 webapp.py
Restart=always
User=$ME

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now livesectional-lite livesectional-lite-web
```

Notes:

- The **display runs as `root`** — the LED library requires it. The
  **web page runs as you** — deliberately unprivileged; Step 7 grants it
  just enough extra to reboot/shutdown, nothing more.
- Check both are alive:

```bash
systemctl status livesectional-lite livesectional-lite-web
systemctl is-enabled livesectional-lite livesectional-lite-web
```

Both should say `active` and `enabled`. **Enabled is what makes them
come back after every reboot with nobody logged in and nothing typed.**

## Step 7 — Let the web page's power buttons work (one-time)

The admin page has **Reboot** and **Shutdown** buttons. They run through
passwordless sudo scoped to a short list of commands. This one line also
gives *you* password-free restarts of the map services from the terminal:

```bash
ME=$(whoami)
echo "$ME ALL=(root) NOPASSWD: /bin/true, /usr/bin/true, /sbin/reboot, /sbin/poweroff, /usr/sbin/reboot, /usr/sbin/poweroff, /bin/systemctl * livesectional-lite*, /usr/bin/systemctl * livesectional-lite*" | sudo tee /etc/sudoers.d/livesectional-lite-power
sudo chmod 440 /etc/sudoers.d/livesectional-lite-power
sudo visudo -c
```

- **Yes, this command asks for your password.** You're using sudo to
  *grant* the exception — the exception can't cover its own creation.
  That's the last password this setup will ask for.
- `visudo -c` must answer **"parsed OK."** If it reports an error, the
  whole file is ignored — re-run the commands and watch for typos.
- Why are `/bin/true` and `/usr/bin/true` on the list? Because the web
  page tests its permission by running `true` (a harmless do-nothing
  command) through sudo. **If `true` isn't on the list, the test fails
  and the page reports "not permitted" even when reboot itself is
  allowed.** (Learned the hard way.)
- Why both `/sbin/...` and `/usr/sbin/...` (and both `systemctl` paths)?
  Current Pi OS merges those folders; sudo matches the path literally,
  so both spellings are listed. Costs nothing, removes a whole class of
  "but it's right there?!" failures.
- Prove it works — this clears sudo's memory (simulating a fresh boot),
  then runs the page's exact test: `READY` means you're done.

```bash
sudo -k
sudo -n /bin/true && echo READY
```

Everything *not* on that list still asks for your password, as it should.

## Step 8 — The full reboot test

On the admin page, press **Reboot** and confirm. Expected sequence:

1. The page says it's rebooting.
2. **The LEDs go dark within a second.** The display blanks the strip
   before the Pi goes down — otherwise a strip on its own power supply
   would sit frozen on its last picture after a shutdown. (You don't
   need to do anything for this; it's built in.)
3. About a minute later the map lights again on its own with fresh data,
   and the admin page loads again. Nobody logged in, nothing typed.
   That's the whole setup working end to end.

For **Shutdown**, expect the same blanking, then the Pi stays off until
you unplug/replug its power. There is no remote wake-up.

---

## Using the admin page

`http://raspberrypi.local:5000` — **no login; home network only. Never
port-forward it to the internet.**

- **Status box:** whether the map is on, when the last fetch happened
  (shown readably, e.g. "Saturday Oct 24th at 9:55 am"), how many
  stations are reporting, and one **"On the map"** line: four colored
  dots — green, blue, red, magenta — each followed by the number of
  LEDs currently lit in that color (the dots' tooltips name the
  categories: VFR, MVFR, IFR, LIFR). A **Pi** line at the bottom shows
  the Pi's hostname and IP address.
- **Collapsible sections:** the Map Builder, Power and Settings
  sections start closed — click a section's header to open it (the
  status box always stays visible). The page remembers, in this
  browser, which sections you left open; a section also opens on its
  own when it has something to show you, like a builder error.
- **Map on/off button:** in the Power section — turns every LED
  (including the legend) dark while weather keeps fetching, so
  turning it back on is instant. The setting survives reboots.
- **Map name:** an optional name (in Settings) shown in the page
  header and the browser tab — worth setting when you run more than
  one map, so the tabs stay easy to tell apart.
- **Brightness:** overall LED brightness, 0–100%.
- **Night mode:** between the start and end times, brightness scales to
  the **night brightness** level (a percentage of normal). Set night
  brightness to **0** and the map goes completely off overnight.
- **High winds:** stations at or above 15 kt (wind or gust) blink in
  their own flight-category color; the wind legend LED blinks VFR
  green on the same cycle.
- **Lightning:** stations reporting thunderstorms keep their
  flight-category color and double-flash bright yellow over it; the
  lightning legend LED uses VFR green as its baseline color for the
  same pattern.
- **Use wipe:** each update blanks the string for a beat, then
  sweeps the new data in, so an update is visible even when no color
  changed. Turn it off and new data simply appears.
- **Reboot / Shutdown:** as set up in Step 7.

Settings are stored in `config.json` and picked up by the display
within a few seconds — no restart needed.

### The Map Builder (entering your airports without counting)

The builder section of the admin page fills in the `airports` file one
position at a time, using the physical map as the reference:

1. Enter an LED number and press **Go** — or just start at LED 0 —
   then press **Light this LED**. The whole string goes dark except
   that one position, lit bright white at full brightness.
2. Look at where the lit LED sits on your map. That place *is* the
   position being edited — the page also shows its line number in the
   `airports` file, the entry currently there, and the entries a couple
   of positions either side, so you can keep your bearings.
3. Type the station ID for that spot (e.g. `KFLG`), or `NULL` for an
   unused position, `LGND` for a legend LED, and press **Save airport**.
   For a legend position, the row of legend buttons is easier: tap
   the one that matches your printed legend card (VFR, MVFR, IFR,
   LIFR, No data, Wind, Storms — or Plain LGND) and the position is
   stored with that legend role pinned to it (e.g. `LGND:WIND`), so
   the LED always shows its label's display wherever the position
   sits in the file. A plain `LGND` instead takes the next role in
   the standard legend sequence (VFR, MVFR, IFR, LIFR, no-data,
   wind, lightning).
4. Press **Next** and repeat — stepping lights each new position
   automatically, so you can walk the entire string in order.
5. When you're done, press **Turn builder light OFF** and the map goes
   back to showing weather.

While the builder light is on, the weather display pauses on that one
LED (weather data keeps updating underneath). Saves take effect on the
map right away — no restart. If you stop mid-build, the position is
remembered (`builder.json`) and the light stays where you left it,
even across a reboot, until you turn it off. Each step or save reloads
the page, and the page scrolls back to where you were in the section —
no hunting for your place on a small screen.

**Already have an airports file?** If you're coming from the original
LiveSectional, its `airports` file is the same format — one entry per
line, in LED order. In the builder section, use **Upload an airports
file** to load the whole list at once instead of typing each station.
The file is checked before anything changes (every line must be a
station ID, `NULL`, or `LGND` — up to 2,500 positions), the upload
replaces the current list, and the map picks it up on its next update.
Your previous list is kept as `airports.bak` in the same folder, so if
you upload the wrong file you can copy it back
(`cp airports.bak airports`).

## How it works (30-second version)

Every 5 minutes the display downloads **one** file from the Aviation
Weather Center (`metars.cache.xml.gz`) containing all current METARs
worldwide, picks out your stations, and renders a frame. If a fetch
fails, the map keeps showing the previous data rather than going dark.
A station with no flight category in the data gets one computed from
its visibility and lowest broken/overcast ceiling, per FAA thresholds.

## Troubleshooting

| Symptom | Cause & fix |
|---|---|
| Service shows `status=200/CHDIR` or `failed` | The `WorkingDirectory=` path in the service file doesn't exist. Re-run Step 6 from inside the real folder (it regenerates the paths), then `sudo systemctl daemon-reload`, `sudo systemctl reset-failed`, `sudo systemctl restart livesectional-lite livesectional-lite-web`. |
| Power buttons say "aren't permitted" | Work through: (1) does `/etc/sudoers.d/livesectional-lite-power` exist? (2) `sudo visudo -c` says parsed OK? (3) is `/bin/true` in the line? (4) does the username in the line match `systemctl show livesectional-lite-web -p User`? Test as the web user: `sudo -u YOURUSER sudo -n /bin/true && echo OK`. |
| `sudo` asks for a password after reboot | Normal for everything *except* the Step 7 list — sudo's memory dies at reboot by design. The map's own commands (reboot, poweroff, service restarts) are covered by the list and never ask. |
| Map dark for a minute after boot | It's fetching. The first frame arrives after the first download; watch for the sweep. |
| One LED always dark | That station isn't reporting (common at night for small fields), or its ID in `airports` has a typo. IDs are case-sensitive and must match the data exactly. |
| Log says "config read failed" | `config.json` is missing from the project folder. The map runs on defaults; copy the file in and restart the display: `sudo systemctl restart livesectional-lite`. |
| Web page won't load | Check `systemctl status livesectional-lite-web`; confirm you're using the right address (`hostname -I` on the Pi) and port **5000**, on the same network. |
| Colors look swapped (red/green) | Your strip's color order differs. Near the top of `metar_lite.py` the strip type is set for GRB strips; ask your strip vendor which order yours uses and change that one constant. |
| Strip frozen lit after a shutdown | You're running an older `webapp.py`/`metar_lite.py` pair without the blank-on-power feature. Replace both files with the current versions and restart both services. |
| Whole string dark except one white LED | The **Map builder light is on** — possibly left on from an earlier session; it survives reboots on purpose so you can resume a build. Open the admin page and press **Turn builder light OFF**. |
| "On the map" counts all read 0 | The counts arrive with the display's status reports; right after an update they fill in at the next fetch (within ~5 minutes), or restart the display to get one immediately: `sudo systemctl restart livesectional-lite`. |

## Updating the programs later

If you cloned in Step 3, use the `git pull` method in the next section
instead. If you placed the files manually, copy the new file(s) into
`~/livesectional-lite` (replacing the old), then:

```bash
sudo systemctl restart livesectional-lite livesectional-lite-web
```

(password-free, thanks to Step 7). Your `config.json` settings and your
`airports` layout are never touched by an update — keep your own copies
of those two files safe; they're the parts that are uniquely yours.

## Keeping the Pi in sync with GitHub

The mental model: **the GitHub repository is the master record, and the
folder on your Pi is a running copy of it. Nothing syncs on its own** —
the maintainer publishes updates to GitHub, and they reach your Pi only
when you deliberately bring them down ("pull"). The flow is one-way:
your Pi never sends anything back to the repository.

You don't have to check GitHub by hand to know whether there's
anything to pull: the admin page's status box checks on its own (at
startup, then hourly) and shows an **"Update available"** line with
the number of new commits whenever the Pi is behind — the line
includes the exact commands to run (the pull, with this Pi's folder
path, and the service restart, with this Pi's service names).
Nothing shows when the Pi is current.

**Getting improvements onto your Pi (pull):**

```bash
cd ~/livesectional-lite
git status
git pull
sudo systemctl restart livesectional-lite livesectional-lite-web
```

`git pull` downloads whatever changed on GitHub and updates your files
in place; the restart puts the new code to work. Run `git status`
first: it reports any files you've changed locally. Git will never
silently overwrite a local change — if GitHub's copy of the *same* file
also changed, the pull stops with an error and leaves yours untouched.

That matters most for your two personal files, `config.json` and
`airports` (the Map Builder edits `airports` right on the Pi; those
edits exist only on the Pi). **Recommended one-time setup** so pulls
never trip over them at all — run this once, in the folder on the Pi:

```bash
git update-index --skip-worktree config.json airports
```

That tells git "these two files are mine; never update them from the
repo and don't report them as changed." Program updates then pull
cleanly every time, and `git status` stays quiet about your files.
(If a pull ever does stop over one of them anyway:
copy your version aside (`cp airports ~/airports.mine`), run
`git checkout -- airports` to accept GitHub's copy, pull again, then
compare and copy yours back if you still want it.) Program files
(`metar_lite.py`, `webapp.py`) have no local edits, so they pull
cleanly every time.

One consequence worth knowing: because nothing syncs automatically, a
Pi that is never pulled stays exactly as it was — which is fine. Update
when there's something you want, not on a schedule.

## Security notes

- The web admin has **no password** — anyone on your network who can
  reach it can change settings or reboot the Pi. Keep it on a trusted
  home network and never expose port 5000 to the internet.
- The display service runs as root because the LED hardware demands it;
  the web service runs as your ordinary user, and its sudo pass covers
  only `true`, the two power commands, and systemctl actions on the two
  map services. That's the deliberate design — don't widen it to
  `NOPASSWD: ALL`.
