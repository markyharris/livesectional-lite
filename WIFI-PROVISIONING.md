# WiFi Provisioning for LS Lite

**Status: INSTALLED AND VERIFIED — Nov 6, 2026.**
Installed on a fresh testbed SD card built from this repo (standard
HOWTO build, `livesectional-lite` services), following the steps
below. `comitup-cli` reported CONNECTED on the home network after
install, and the gift-flow rehearsal — delete the connection in
comitup-cli, Pi drops to HOTSPOT, phone joins the setup AP, portal
re-adds the home WiFi, Pi reconnects — worked properly.
Researched Nov 5, 2026, from Comitup's official site/repo/docs and
Debian's package records. Items that remain unverified are collected
at the end.

## The problem

A built map knows exactly one WiFi network: the one it was set up on.
Gift the map or move it to a new house and it can't get online, and
there's no keyboard or screen to tell it the new credentials.

## Recommendation

Install **Comitup** (by David Steele, a Debian developer) on the Pi.
It is a small service built for exactly this: it manages WiFi through
NetworkManager, and when the Pi can't join any network it knows, it
raises its own access point with a captive-portal page where a phone
picks the new network and types the password. No changes to the LS Lite
code; it is a package install plus one small config file, and it is
removable in one command.

## How Comitup behaves

Sources: project home page, the comitup(8) and comitup-cli man pages.

- Three states: **HOTSPOT**, **CONNECTING**, **CONNECTED**.
- On boot it tries the WiFi connections NetworkManager already knows.
  If one connects, that's it — normal operation, hotspot never appears.
- If none connect, it raises a hotspot named **comitup-<nnn>**, where
  <nnn> is a persistent number unique to that Pi. The setup page is at
  **http://10.41.0.1** and at **http://comitup-<nnn>.local** (mDNS).
- Joining the hotspot triggers the phone's captive-portal sign-in sheet
  automatically (iOS, Android, macOS, Firefox). The page lists the
  networks the Pi can hear; pick one, enter its password, Connect.
  On success the hotspot is replaced by the real connection — with a
  single WiFi radio (our case) the AP goes away as the Pi joins.
- Comitup remembers **multiple** networks and searches among them on
  each attempt, so a map that has been set up at two houses knows both.
- While sitting in HOTSPOT it retries its known connections roughly
  every 3 minutes (current man page; older copies say ~1 minute), and
  skips the retry while a phone is connected to the hotspot, so it
  won't drop a user mid-setup.
- If an established connection is later lost, Comitup returns to
  HOTSPOT and keeps retrying known networks from there — it self-heals
  when the router comes back.
- The portal has a "Locate" button that blinks the Pi's green LED, to
  tell multiple maps apart.
- `comitup-cli` on the Pi shows state and can delete connections.

## Current state of the project (verified Nov 5, 2026)

- Actively maintained. Latest release **1.47.1-2, Apr 24, 2026**, with
  four releases that month; the comitup(8) man page is dated April 2026.
  GitHub repo: 815 commits, not archived.
- Debian Bookworm's own archive carries an older **1.38-2~deb12u1**.
  The project warns that Bookworm additionally needs an **updated
  python3-networkmanager**, available from the Comitup apt repository —
  so the recommended install route is the project's repo, not stock
  Bookworm packages. The repo also keeps comitup current across
  `apt upgrade` runs.
- Raspberry Pi OS Bookworm already uses NetworkManager by default,
  which Comitup requires (plus systemd). Nothing to switch.

## Fit with LS Lite

Checked against the Lite source in this repo:

- **No networking role to conflict with.** Neither `metar_lite.py` nor
  `webapp.py` touches network configuration; the Lite services just use
  whatever connectivity the OS gives them. Comitup only changes who
  manages WiFi below them.
- **Admin page on the setup AP: yes.** `webapp.py` binds
  `0.0.0.0:5000`, i.e. every interface, so while the Pi is in HOTSPOT
  mode the full Lite admin is reachable at **http://10.41.0.1:5000** —
  handy for Mark during a test. The captive portal itself lives on
  port 80 and doesn't intercept 5000; you browse to it directly.
  (The admin has no login, as its own header notes — one more reason
  to put a password on the setup AP, below.)
- **Do NOT set Comitup's `web_service` option.** That option exists to
  stop a user web service while in HOTSPOT and start it when CONNECTED.
  We want the opposite — admin up in both states — so leave it unset.
  The Lite services stay enabled and running throughout; the display
  loop already tolerates having no internet (stations show the dim
  "no weather" state and it recovers on the next fetch cycle).
- **Hostname/mDNS:** Comitup publishes its own `comitup-<nnn>.local`
  (or your renamed equivalent) via Avahi in all states, independent of
  the Pi's hostname. The Lite status box's Pi hostname/IP line and LAN
  access to the admin are unaffected once the map is on a real network.
- **Install warning:** the wiki's prep list includes legacy Raspbian
  steps (masking dhcpcd/wpa-supplicant, deleting /etc/network/interfaces
  content, renaming wpa_supplicant.conf). On a stock Bookworm Pi some
  of those services don't exist as standalone units. Check each one on
  the actual Pi before masking anything — see Open Questions.

## Config worth setting — /etc/comitup.conf

From the comitup-conf(5) man page:

```ini
# Friendlier SSID for a gifted map. The Nov 6 testbed used this
# fixed name; LiveSectional-<nnn> keeps each map unique instead.
ap_name: LiveSectional-Setup
# Require a password to join the setup AP. Print it on the card that
# goes in the box with the map. Without this, the setup AP is open.
ap_password: <pick something simple and printable>
```

Options considered and deliberately left off:

- `web_service` — would take the Lite admin down during setup (above).
- `enable_nuke` — hardware factory reset (short GPIO pins 39/40 for
  3 seconds wipes all WiFi settings). Interesting for a gift map with
  no other recovery path, but it also means anyone with physical
  access can wipe the map's WiFi. Off by default; decide per-map.
- `external_callback` — script hook on state changes. Not needed;
  the Lite display loop recovers on its own when connectivity returns.

## Install steps (Mark, once, per map)

Do this with an **Ethernet cable plugged in or a keyboard/monitor
attached** — your SSH-over-WiFi session will drop when WiFi is
reconfigured and at the reboot.

1. Note the map's current SSID and confirm it's a NetworkManager
   connection: `nmcli connection show`.
2. Add the Comitup apt repository: download the
   `davesteele-comitup-apt-source` .deb from the Comitup site
   (linked from the project home page), then
   `sudo dpkg -i davesteele-comitup-apt-source*.deb`.
3. `sudo apt-get update && sudo apt-get install comitup`
   (pulls current comitup plus the updated python3-networkmanager
   Bookworm needs).
4. Pre-flight checks from the project's install page, adapted:
   - No WiFi references in `/etc/network/interfaces`.
   - Nothing else squatting on DHCP/DNS ports for the hotspot
     (a globally-running dnsmasq service is the known offender;
     Comitup runs its own private dnsmasq instance).
   - WLAN country/regulatory domain is set (recent Raspberry Pi OS
     images keep WiFi off until it is) — `raspi-config` → Localisation.
   - Verify — don't blindly apply — the wiki's service-masking list
     against what actually exists on this Bookworm install.
5. Write `/etc/comitup.conf` as above.
6. Reboot. At home it should join your WiFi exactly as before.
   Confirm: `comitup-cli` shows CONNECTED, `systemctl status
   livesectional-lite livesectional-lite-web` are active (on Mark's
   original maps the services are named metar-lite / metar-lite-web),
   and the admin page answers on the LAN.
7. **Rehearse the gift flow at home** (next section): in `comitup-cli`,
   delete the current connection. The Pi drops to HOTSPOT. Join its AP
   from your phone and re-add your home WiFi through the portal. If
   that round-trips, the recipient flow is proven on this unit.

## End-user flow at the new house

What the gift recipient does — suitable for a printed card in the box:

1. Hang the map and plug it in. Give it a minute or two.
2. On your phone, open WiFi settings and join the setup network
   named on the card (the testbed used **LiveSectional-Setup**).
   Enter the setup password from the card if asked.
3. A sign-in page opens by itself, listing the WiFi networks the map
   can see. (If it doesn't open, browse to **10.41.0.1**.)
4. Tap your home network, type its password, tap Connect.
5. The map's WiFi network disappears — that's success. Reconnect your
   phone to your home WiFi if it doesn't switch back on its own.
6. The map starts lighting up with live weather within a few minutes.

Nothing else is needed for normal use; the map remembers the network
across power cycles and outages.

## Before gifting a map

- Delete your home connection from the Pi (`comitup-cli` delete, or
  `nmcli connection delete <your SSID>`) so your WiFi password doesn't
  travel with the card. With no known networks, the map boots straight
  into HOTSPOT at the new house.
- Confirm the airports list and config on the map are the ones the
  recipient should have — provisioning only changes WiFi.
- Include the AP name and AP password (if set) on the card in the box.

## Risks and rollback

- **Transient outage at home:** if the router reboots or the ISP drops,
  Comitup will eventually fall back to HOTSPOT and broadcast the setup
  SSID until the home network returns, retrying every few minutes and
  rejoining on its own. Two consequences: the map shows no-data
  meanwhile (it would anyway — there's no internet), and an
  unpassworded setup AP is briefly joinable by anyone in radio range.
  The `ap_password` setting closes most of that concern.
- **Wrong-password lock-in:** if a recipient fat-fingers their WiFi
  password, the connection fails and Comitup returns to HOTSPOT so
  they can try again — no brick state. (Comitup also drops stored
  credentials that fail authentication, rather than retrying them
  forever, per its changelog.)
- **Install session loss:** expect to lose SSH-over-WiFi during
  install/reboot. Ethernet or local console only.
- **Rollback:** `sudo systemctl disable --now comitup` hands WiFi back
  to plain NetworkManager — the connections themselves are ordinary
  NM connections and stay put. Full removal:
  `sudo apt-get remove comitup`, plus remove the
  `davesteele-comitup-apt-source` package if you added the repo.
  Note the updated python3-networkmanager from the Comitup repo will
  not downgrade itself automatically; it is a support library used by
  Comitup, and leaving it in place is expected to be harmless, but
  this was not verified.

## Alternative considered: build it into the Lite webapp

We could write our own fallback: a watcher that notices NetworkManager
has no connection, raises a hotspot with
`nmcli device wifi hotspot`, runs a dnsmasq instance for DHCP and
portal redirects, and serves a setup form from Flask. Every piece of
that already exists inside Comitup, hardened over a decade — the state
machine, the retry-while-in-hotspot behaviour, portal quirks per phone
OS, unicode SSIDs, authentication-failure handling. Building it in
would also put network administration inside an app that deliberately
has no login and no networking role today, and every map would carry
that risk. Comitup is a package, a three-line config, and one
`systemctl disable` to undo. **Comitup first**; revisit only if it
misbehaves on the real hardware.

## Open questions / not verified from the sandbox

- Which services actually exist on the Pi — resolved Nov 6, 2026 on
  the stock Imager testbed build: NetworkManager WiFi connection
  present, no /etc/network/interfaces file at all, and no system
  dnsmasq unit. None of the wiki's legacy masking steps were needed.
- The exact comitup version the project's apt repo serves for Bookworm
  today (the repo page doesn't publish a version list); expected to be
  current 1.47.x.
- How long a brief home-network dropout lasts before Comitup leaves
  CONNECTED for HOTSPOT on this hardware — the docs state the
  transition happens on connection loss and that HOTSPOT retries every
  ~3 minutes, but NetworkManager's own reconnect attempts happen
  first, and the combined timing was not measured.
- Whether the Lite admin's mDNS/hostname behaviour on the recipient's
  LAN matches home (depends on their router; the admin is a bonus —
  normal map operation doesn't need it).
- Leaving the repo's python3-networkmanager in place after a rollback
  (above).

## Sources

- Comitup project home page — http://davesteele.github.io/comitup/
- Comitup repository (apt) instructions — http://davesteele.github.io/comitup/ppa.html
- GitHub repo — https://github.com/davesteele/comitup
- Installing Comitup (wiki) — https://github.com/davesteele/comitup/wiki/Installing-Comitup
- Debian changelog (release dates/versions) — https://github.com/davesteele/comitup/blob/debian/debian/changelog
- comitup(8) man page — http://davesteele.github.io/comitup/man/comitup.pdf
- comitup-cli man page (state machine) — http://davesteele.github.io/comitup/man/comitup-cli.pdf
- comitup-conf(5) man page (config options) — http://davesteele.github.io/comitup/man/comitup-conf.pdf
- Comitup FAQ (wiki mirror) — https://github-wiki-see.page/m/davesteele/comitup/wiki/FAQ
- Debian package versions by suite — https://qa.debian.org/madison.php?package=comitup
- Raspberry Pi forum: Bookworm uses NetworkManager by default — https://forums.raspberrypi.com/viewtopic.php?p=2147783&sid=345a72ec48e76d117817bca0de145a7d
