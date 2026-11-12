"""
Camel Guard — Local Dashboard Server (camel crossing highway safety, miniature simulator)
------------------------------------------------------------------------------------------
Uses only the Python standard library (no pip install needed).

  python server.py            -> open http://localhost:5100 (or http://<PC-IP>:5100)

HTTP endpoints:
  POST /api/telemetry   <- ESP32 sends data every second; the response carries one queued command
  GET  /api/stream      -> realtime Server-Sent Events to the browser
  Several ESP32s (crossings) can post at once; each is keyed by its MAC address. Requests that act
  on a device carry {"device": <key>} (optional when only one device is known).
  GET  /api/devices     -> every known crossing with its latest telemetry
  POST /api/reset       -> CLEAR on one crossing (manual alert off, holds cleared), or {"all": true}
  POST /api/alert       -> {"on": true|false} manual alert on every crossing (warning signs flash)
  POST /api/test        -> {"device", "out": buzzer|relay|signals|signs|none, "sec"} output test on one device
  GET  /api/team        -> authors (max 4) and supervisors (max 2, min 1) with photo URLs
  POST /api/team/save   -> add / edit one person, optional photo as a data: URL (this PC only)
  POST /api/team/delete -> remove one person and their photo (this PC only)
  POST /api/command     -> reboot / factory reset on the device
  POST /api/config      -> change detection settings on the device (stored in its NVS)
  POST /api/wifi        -> change the device's saved WiFi networks and dashboard address
  POST /api/devices/update -> location (kept on this PC) and name (sent to the device)
  POST /api/devices/forget -> remove an offline device from the list
  GET  /api/settings    -> device settings as last reported by the device
  GET  /api/logs        -> filtered + paginated event log,  /api/logs.csv -> CSV download
  GET  /api/history     -> hazard history (JSON),           /api/history.csv -> CSV download
  GET  /api/vehicles    -> recent vehicles (side, time),    /api/vehicles.csv -> CSV download

  System (only from this PC — http://localhost — and only from the dashboard page itself):
  GET  /api/system             -> ports, firewall, COM ports, Arduino tools, firmware versions
  POST /api/system/ports       -> save dashboard / discovery / USB COM ports (camel_guard.ini)
  POST /api/system/check-port  -> is a port free?
  POST /api/system/firewall    -> update the Windows Firewall rule (one admin prompt)
  POST /api/system/restart     -> restart the dashboard on the saved ports
  POST /api/firmware           -> {"action": compile|upload|check|install} (background job)
  GET  /api/firmware/job       -> job progress and log

UDP discovery (port from camel_guard.ini, default 45811):
  The ESP32 broadcasts "CAMELGUARD_DISCOVER" when it cannot reach the dashboard; we answer
  "CAMELGUARD_HERE:<ip>:<port>" so the device finds this laptop again after a DHCP change.
"""
import base64
import csv
import hmac
import io
import json
import os
import re
import socket
import sqlite3
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "..", "tools"))
import firmware_tool  # noqa: E402
import project_settings  # noqa: E402

# Ports come from camel_guard.ini (edited in Settings > Firmware & ports); env vars override for tests.
_saved = project_settings.load()
PORT = int(os.environ.get("PORT") or _saved["dashboard"])
DISCOVERY_PORT = int(os.environ.get("DISCOVERY_PORT") or _saved["discovery"])
# START_DASHBOARD.bat sets this and restarts the server when it exits with RESTART_CODE.
SUPERVISED = os.environ.get("CG_SUPERVISED") == "1"
# Cloud mode (Render sets RENDER, or CG_CLOUD=1): the dashboard is on the internet. Every page and API then needs
# DASHBOARD_PASSWORD (HTTP Basic, any user name); the ESP32's POST /api/telemetry needs the X-Device-Key header when
# DEVICE_KEY is set. Both come from environment variables only. Locally both are normally unset = no login, as before.
CLOUD = os.environ.get("CG_CLOUD") == "1" or bool(os.environ.get("RENDER"))
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
DEVICE_KEY = os.environ.get("DEVICE_KEY", "")
# Identifies this copy of server.py. index.html is read from disk on every request, so after an
# update the page can be newer than a server that is still running; the page compares the two.
SERVER_BUILD = str(int(os.path.getmtime(os.path.abspath(__file__))))
RESTART_CODE = 3
DATA_DIR = os.path.join(BASE_DIR, "data")
PHOTO_DIR = os.path.join(DATA_DIR, "photos")
TEAM_LIMITS = {"author": (0, 4), "supervisor": (1, 2)}   # (min kept on delete, max)
PHOTO_MAX_BYTES = 2 * 1024 * 1024
STATIC_DIR = os.path.join(BASE_DIR, "static")
WIRING_SVG = os.path.join(BASE_DIR, "..", "docs", "wiring_diagram.svg")
DB_PATH = os.path.join(DATA_DIR, "road_log.db")
OFFLINE_AFTER_S = 10   # telemetry every 1 s; phone hotspots can pause a few seconds
MAX_WIFI_NETWORKS = 5
TEST_OUTPUTS = {"buzzer": "buzzer", "signals": "yellow lamp", "signs": "warning signs",
                "leds": "each LED one by one"}
TEST_MAX_S = 30        # same as TEST_MAX_SEC in config.h
TEST_TTL_S = 15        # a test that cannot be delivered quickly is dropped, never run late
HAZARD_STATES = ("WARNING", "DANGER")
LEVEL_NUM = {"WARMUP": 0, "CLEAR": 0, "CAUTION": 1, "WARNING": 2, "DANGER": 3}
KIND_TEXT = {"camel": "Camel crossing the highway", "motion": "Motion at the crossing at night",
             "manual": "Manual alert"}

os.makedirs(DATA_DIR, exist_ok=True)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- settings
# Limits: the dashboard must never be able to make the system blind.
# (lo, hi, decimals) — values outside the range are clamped, not rejected.
CONFIG_LIMITS = {
    "carCm":       (3, 150, 0),    # a vehicle HC-SR04 reading at or below this (cm) = a vehicle
    "confirm":     (1, 20, 0),     # a camel IR must be blocked this many 50 ms readings in a row
    "holdSec":     (2, 60, 0),
    "approachSec": (1, 20, 0),
}
CONFIG_FLAGS = ["buzzer", "useA", "useB"]   # booleans (useA/B = camel IR fitted)
CONFIG_TEXT = {"name": 24}         # string fields -> max length


def clean_config(raw):
    """Validate & clamp a settings patch coming from the browser."""
    out = {}
    for key, (lo, hi, dec) in CONFIG_LIMITS.items():
        if key not in raw:
            continue
        try:
            v = float(raw[key])
        except (TypeError, ValueError):
            continue
        v = max(lo, min(hi, v))
        out[key] = int(round(v)) if dec == 0 else round(v, dec)
    for key in CONFIG_FLAGS:
        if key in raw:
            out[key] = bool(raw[key])
    for key, maxlen in CONFIG_TEXT.items():
        if key in raw:
            # Same character set as sanitizeName() in the firmware
            v = re.sub(r"[^A-Za-z0-9 _.\-]", "", str(raw[key])).strip()[:maxlen]
            if v:
                out[key] = v
    return out


def power_point(t):
    """Battery / solar values for the 10-minute charts (None = sensor not fitted)."""
    def num(k):
        v = t.get(k)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    bat, pv = bool(t.get("batOk")), bool(t.get("pvOk"))
    pvv, pvma = num("pvV"), num("pvmA")
    return {"batV": num("batV") if bat else None, "batmA": num("batmA") if bat else None,
            "pvV": pvv if pv else None,
            "pvW": round(pvv * pvma / 1000.0, 2) if pv and pvv is not None and pvma is not None else None}


# ---------------------------------------------------------------- database
class Store:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS hazards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device TEXT, device_key TEXT, location TEXT, started_at TEXT, ended_at TEXT,
                kind TEXT, peak_level TEXT, vehicles INTEGER, max_speed REAL, overspeed INTEGER
            );
            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, device TEXT, device_key TEXT, location TEXT,
                speed REAL, dir TEXT, state TEXT, overspeed INTEGER
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, device TEXT, level TEXT, message TEXT, detail TEXT
            );
            CREATE TABLE IF NOT EXISTS team (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT, name TEXT, idno TEXT, email TEXT, photo TEXT, sort INTEGER
            );
            CREATE TABLE IF NOT EXISTS devices (
                key TEXT PRIMARY KEY,          -- MAC address
                mac TEXT, name TEXT, location TEXT,
                first_seen TEXT, last_seen TEXT, fw TEXT, ip TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
            CREATE INDEX IF NOT EXISTS idx_vehicles_ts ON vehicles(ts);
            """
        )
        self.db.commit()

    def execute(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid

    def query(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def scalar(self, sql, args=()):
        with self.lock:
            row = self.db.execute(sql, args).fetchone()
            return row[0] if row else 0


store = Store(DB_PATH)


# ---------------------------------------------------------------- live state
MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
CMD_TTL_S = 600          # a queued command is dropped (and logged) if the device has not taken it by then
ALERT_TTL_S = 60         # "alert" is only useful right now — never deliver it to a crossing that comes back later


def kinds_of(hazard):
    return [k for k in str(hazard or "").split("+") if k]


def kind_text(kinds):
    return " + ".join(KIND_TEXT.get(k, k) for k in kinds) or "–"


class Dev:
    """One ESP32 (or simulated device). Keyed by its MAC address, so renaming it is harmless."""

    def __init__(self, key, meta):
        self.last_up = None        # telemetry uptime, to notice restarts
        self.key = key
        self.meta = dict(meta)          # mac, name, location, first_seen (persisted in the devices table)
        self.latest = None
        self.last_seen = 0.0
        self.saved_at = 0.0             # last time last_seen/fw/ip were written to the database
        self.online = False
        self.history = deque(maxlen=600)  # 10 minutes for the charts
        self.state = None               # last reported road state (None = unknown / was offline)
        self.open_hazard = None         # row id of the hazard episode that is still open
        self.peak = None                # {"kinds": set, "level", "vehicles", "max_speed", "overspeed"}
        self.last_veh = None            # vehCount seen last, to notice new vehicles
        self.last_faults = ""
        self.last_chg = None            # last charge state worth an event: charging / full / discharging
        self.last_low = False           # battery low reported
        self.queue = []                 # commands waiting for this device: {"id","body","label","tries","expires"}
        self.last_cmd = None            # {"id", "label", "state": sent|applied|lost, "at"}
        self.cfg = {}                   # settings as last reported by the device
        self.nets = []                  # SSIDs saved on the device (never passwords)

    @property
    def name(self):
        return (self.latest or {}).get("id") or self.meta.get("name") or self.key

    @property
    def location(self):
        return self.meta.get("location") or ""

    def view(self):
        return {
            "key": self.key, "name": self.name, "location": self.location, "mac": self.meta.get("mac") or "",
            "online": self.online, "firstSeen": self.meta.get("first_seen"),
            "lastSeen": datetime.fromtimestamp(self.last_seen).strftime("%Y-%m-%d %H:%M:%S") if self.last_seen
            else self.meta.get("last_seen"),
            "latest": self.latest, "cfg": self.cfg, "nets": self.nets, "cmd": self.last_cmd,
            "queued": len(self.queue),
        }


class Hub:
    """All crossings, their hazard episodes and command queues, the manual alert, and the SSE fan-out."""

    def __init__(self):
        self.cond = threading.Condition()
        self.version = 0
        self.lock = threading.RLock()
        self.devs = {}
        for row in store.query("SELECT * FROM devices"):
            self.devs[row["key"]] = Dev(row["key"], row)
        self.alert = {"active": False, "since": None, "by": ""}
        self._today = {"t": 0, "data": {}}
        # Ids must not repeat across server restarts: a device keeps echoing the id of the
        # last command it handled, so a new command that reused that id would be marked
        # "applied" without ever being sent. Seed from the clock (fits in int32).
        self.cmd_seq = int(time.time() * 1000) % 2_000_000_000

    # ---- notifications
    def notify(self):
        with self.cond:
            self.version += 1
            self.cond.notify_all()

    def log_event(self, level, message, detail="", device=""):
        store.execute(
            "INSERT INTO events(ts, device, level, message, detail) VALUES (?,?,?,?,?)",
            (now_str(), device, level, message, detail),
        )

    # ---- devices
    @staticmethod
    def key_of(t):
        mac = str(t.get("mac") or "").strip().upper()
        if MAC_RE.match(mac):
            return mac
        return "id:" + str(t.get("id") or "esp32")[:24]

    def resolve(self, key):
        """The device a request is about: the given key, or the only device if there is just one."""
        with self.lock:
            if key and key in self.devs:
                return self.devs[key]
            if not key and len(self.devs) == 1:
                return next(iter(self.devs.values()))
        return None

    def sorted_devs(self):
        rank = {"DANGER": 0, "WARNING": 1, "CAUTION": 2}
        with self.lock:
            return sorted(self.devs.values(), key=lambda d: (
                rank.get((d.latest or {}).get("state") if d.online else None, 3),
                0 if d.online else 1, d.name.lower()))

    def update_meta(self, dev, **fields):
        fields = {k: v for k, v in fields.items() if k in ("name", "location")}
        if not fields:
            return
        dev.meta.update(fields)
        sets = ", ".join(f"{k}=?" for k in fields)
        store.execute(f"UPDATE devices SET {sets} WHERE key=?", list(fields.values()) + [dev.key])
        self.notify()

    def forget(self, dev):
        with self.lock:
            self.devs.pop(dev.key, None)
        store.execute("DELETE FROM devices WHERE key=?", (dev.key,))
        self.log_event("info", "Device removed from the list", dev.name, dev.name)
        self.notify()

    def _new_device(self, key, t):
        name = str(t.get("id") or key)
        meta = {"mac": key if MAC_RE.match(key) else "", "name": name, "location": "", "first_seen": now_str()}
        store.execute("INSERT OR REPLACE INTO devices(key, mac, name, location, first_seen) VALUES (?,?,?,?,?)",
                      (key, meta["mac"], name, meta["location"], meta["first_seen"]))
        dev = self.devs[key] = Dev(key, meta)
        self.log_event("info", "New crossing detected",
                       f"{name} ({t.get('ip', '?')}) — give it a location in Settings > General", name)
        return dev

    # ---- command queue (one per device)
    def enqueue(self, dev, body, label, ttl=CMD_TTL_S, urgent=False):
        """body: dict sent verbatim to the device. Returns the command id."""
        with self.lock:
            self.cmd_seq += 1
            item = {"id": self.cmd_seq, "body": {**body, "id": self.cmd_seq}, "label": label,
                    "tries": 0, "expires": time.time() + ttl}
            if urgent:            # CLEAR, alert and tests go before queued settings
                dev.queue.insert(0, item)
            else:
                dev.queue.append(item)
            dev.last_cmd = {"id": self.cmd_seq, "label": label, "state": "sent", "at": now_str()}
        self.notify()
        return self.cmd_seq

    def take_command(self, dev, ack):
        """Called for every telemetry POST: returns the command to put in the response."""
        now = time.time()
        with self.lock:
            for it in list(dev.queue):
                if ack and it["id"] == ack:          # the device confirms it has applied this one
                    dev.queue.remove(it)
                    self.log_event("info", "Setting applied", it["label"], dev.name)
                    dev.last_cmd = {"id": it["id"], "label": it["label"], "state": "applied", "at": now_str()}
            for it in list(dev.queue):
                if it["expires"] < now:
                    dev.queue.remove(it)
                    self.log_event("warning", "Command not delivered", it["label"], dev.name)
                    dev.last_cmd = {"id": it["id"], "label": it["label"], "state": "lost", "at": now_str()}
            if not dev.queue:
                return {"cmd": "none"}
            head = dev.queue[0]
            head["tries"] += 1
            if head["tries"] > 12:
                dev.queue.pop(0)
                self.log_event("warning", "Setting not confirmed by the device", head["label"], dev.name)
                dev.last_cmd = {"id": head["id"], "label": head["label"], "state": "lost", "at": now_str()}
                return {"cmd": "none"}
            return head["body"]

    # ---- telemetry
    def on_telemetry(self, t):
        t["ts"] = time.time()
        state = t.get("state", "CLEAR")
        key = self.key_of(t)
        with self.lock:
            dev = self.devs.get(key)
            is_new = dev is None
            if is_new:
                dev = self._new_device(key, t)      # logs "New crossing detected"
            name = str(t.get("id") or dev.name)
            if not dev.online and not is_new:
                self.log_event("info", "Device back online" if dev.last_seen else "Device connected",
                               f"{name} ({t.get('ip', '?')})", name)
            up = t.get("up")
            restarted = isinstance(up, (int, float)) and isinstance(dev.last_up, (int, float)) and up < dev.last_up
            if restarted:
                why = str(t.get("boot") or "unknown")[:40]
                bad = any(k in why for k in ("crash", "watchdog", "brownout"))
                self.log_event("warning" if bad else "info", "Device restarted", f"reason: {why}", name)
                dev.last_veh = None
            if isinstance(up, (int, float)):
                dev.last_up = up
            prev_state = dev.state
            dev.state = state
            dev.online = True
            dev.last_seen = t["ts"]
            if isinstance(t.get("cfg"), dict):
                dev.cfg = t["cfg"]
            if isinstance(t.get("nets"), list):
                dev.nets = [str(s)[:32] for s in t["nets"]]
            if name != dev.meta.get("name") or t["ts"] - dev.saved_at > 30:
                dev.meta["name"] = name
                dev.saved_at = t["ts"]
                store.execute("UPDATE devices SET name=?, last_seen=?, fw=?, ip=? WHERE key=?",
                              (name, now_str(), t.get("fw"), t.get("ip"), key))

            self._transitions(dev, t, prev_state, state, name)
            self._vehicles(dev, t, state, name)
            self._housekeeping(dev, t, name)

            dev.latest = t
            dev.history.append({"ts": t["ts"], "camel": int(bool(t.get("camel"))),
                                "veh": int(bool(t.get("veh"))), "lvl": LEVEL_NUM.get(state, 0), "state": state,
                                **power_point(t)})
            reply = self.take_command(dev, t.get("ack"))
        self.notify()
        return reply

    def _transitions(self, dev, t, prev, state, name):
        where = f" — {dev.location}" if dev.location else ""
        kinds = kinds_of(t.get("hazard"))
        # cars detected at the 400 m point of each side (HC-SR04): that side's warning signs flash fast
        sides = " + ".join(s for s in ("A", "B") if t.get("veh" + s))
        coming = f" · vehicle approaching from side {sides}" if sides else " · vehicle approaching"
        if state in HAZARD_STATES:
            if dev.open_hazard is None:
                dev.peak = {"kinds": set(kinds), "level": state, "vehicles": 0, "max_speed": None, "overspeed": 0}
                dev.open_hazard = store.execute(
                    "INSERT INTO hazards(device, device_key, location, started_at, kind, peak_level, vehicles,"
                    " max_speed, overspeed) VALUES (?,?,?,?,?,?,?,?,?)",
                    (name, dev.key, dev.location, now_str(), "+".join(kinds), state, 0, None, 0))
                title = ("CAMEL CROSSING" if "camel" in kinds else "MOTION AT NIGHT" if "motion" in kinds
                         else "MANUAL ALERT")
                came = f" · from side {t['camelDir']} (IR {t['camelDir']})" if "camel" in kinds and t.get("camelDir") else ""
                self.log_event("alarm" if state == "DANGER" or "camel" in kinds else "warning", title + where,
                               kind_text(kinds) + came + (coming if state == "DANGER" else ""), name)
            else:
                pk = dev.peak
                new = [k for k in kinds if k not in pk["kinds"]]
                if new:
                    pk["kinds"].update(new)
                    self.log_event("warning", "Hazard grew" + where, "now: " + kind_text(sorted(pk["kinds"])), name)
                if state == "DANGER" and pk["level"] != "DANGER":
                    pk["level"] = "DANGER"
                if state == "DANGER" and prev == "WARNING":
                    self.log_event("alarm", "DANGER — vehicle approaching the hazard" + where, kind_text(kinds) + coming, name)
        elif dev.open_hazard is not None:
            self.close_hazard(dev)
            self.log_event("info", "Road clear again" + where,
                           "device restarted" if state == "WARMUP" else "camel gate clear", name)
        if prev == "WARMUP" and state in ("CLEAR", "CAUTION"):
            self.log_event("info", "Warm-up finished", "System armed", name)

    def _vehicles(self, dev, t, state, name):
        cnt = t.get("vehCount")
        if not isinstance(cnt, int):
            return
        if dev.last_veh is not None and cnt > dev.last_veh:
            speed = t.get("lastSpeed")
            speed = round(speed, 1) if isinstance(speed, (int, float)) and speed >= 0 else None
            over = bool(t.get("lastOver"))
            store.execute("INSERT INTO vehicles(ts, device, device_key, location, speed, dir, state, overspeed)"
                          " VALUES (?,?,?,?,?,?,?,?)",
                          (now_str(), name, dev.key, dev.location, speed, t.get("lastDir") or "", state, int(over)))
            if dev.peak is not None and dev.open_hazard is not None:
                dev.peak["vehicles"] += cnt - dev.last_veh
                if speed is not None and (dev.peak["max_speed"] is None or speed > dev.peak["max_speed"]):
                    dev.peak["max_speed"] = speed
                if over:
                    dev.peak["overspeed"] += 1
            if over:
                lim = (dev.cfg or {}).get("speedLimit", "?")
                self.log_event("alarm", "Vehicle speeding through a hazard",
                               f"{speed:.0f} km/h (limit {lim} km/h) · {t.get('hazard') or 'hazard'}", name)
        dev.last_veh = cnt

    def _housekeeping(self, dev, t, name):
        # Battery and solar panel (INA219 modules; absent on a USB-only device)
        if t.get("batOk"):
            chg = t.get("chg")
            if chg in ("charging", "full", "discharging") and chg != dev.last_chg:
                if dev.last_chg is not None:
                    bat = f"{t.get('batV', 0):.2f} V · ≈ {t.get('batPct', 0)} % · {t.get('batmA', 0):+.0f} mA"
                    if chg == "charging":
                        self.log_event("info", "Battery charging", f"{bat} · panel {t.get('pvV', 0):.1f} V", name)
                    elif chg == "full":
                        self.log_event("info", "Battery full", bat, name)
                    else:
                        self.log_event("info", "Running on the battery", bat + (" · no sun" if not t.get("sun") else ""), name)
                dev.last_chg = chg
        low = "BAT-LOW" in str(t.get("faults") or "").split()
        if low != dev.last_low:
            if low:
                self.log_event("warning", "Battery low", f"{t.get('batV', 0):.2f} V (≈ {t.get('batPct', 0)} %) — charge it or connect USB", name)
            else:
                self.log_event("info", "Battery recovered", f"{t.get('batV', 0):.2f} V", name)
            dev.last_low = low
        faults = " ".join(f for f in str(t.get("faults") or "").split() if f != "BAT-LOW")
        if faults != dev.last_faults:
            if faults:
                self.log_event("warning", "Sensor fault", faults, name)
            elif dev.last_faults:
                self.log_event("info", "Sensor fault cleared", dev.last_faults, name)
            dev.last_faults = faults

    def close_hazard(self, dev):
        rid, dev.open_hazard = dev.open_hazard, None
        if rid is None:
            return
        pk, dev.peak = dev.peak or {}, None
        store.execute(
            "UPDATE hazards SET ended_at=?, kind=?, peak_level=?, vehicles=?, max_speed=?, overspeed=? WHERE id=?",
            (now_str(), "+".join(sorted(pk.get("kinds") or [])), pk.get("level"), pk.get("vehicles", 0),
             pk.get("max_speed"), pk.get("overspeed", 0), rid),
        )

    def request_reset(self, dev):
        self.log_event("info", "CLEAR pressed", "from the dashboard", dev.name)
        return self.enqueue(dev, {"cmd": "reset"}, "CLEAR", urgent=True)

    def reset_all(self):
        """CLEAR on every online crossing that shows a hazard or a manual alert."""
        with self.lock:
            targets = [d for d in self.devs.values() if d.online and d.latest and
                       (d.latest.get("state") in HAZARD_STATES or d.latest.get("alert"))]
        for d in targets:
            self.request_reset(d)
        return [d.name for d in targets]

    # ---- manual alert: warn at every crossing from the dashboard
    def set_alert(self, on, by="dashboard"):
        with self.lock:
            targets = list(self.devs.values())
            for d in targets:
                d.queue = [it for it in d.queue if it["body"].get("cmd") != "alert"]
                self.enqueue(d, {"cmd": "alert", "on": bool(on)},
                             "Alert all crossings" if on else "Stop manual alert",
                             ttl=ALERT_TTL_S if on else CMD_TTL_S, urgent=True)
            self.alert = {"active": bool(on), "since": now_str() if on else None, "by": by}
        offline = [d.name for d in targets if not d.online]
        detail = f"{len(targets)} crossing(s), from the {by}" + (f"; offline now: {', '.join(offline)}" if offline else "")
        self.log_event("alarm" if on else "info", "MANUAL ALERT STARTED" if on else "Manual alert stopped", detail)
        self.notify()
        return {"crossings": len(targets), "offline": offline}

    def today(self):
        """Counts for the Overview page (cached for a few seconds: the SSE payload is built often)."""
        if time.time() - self._today["t"] > 3:
            day = datetime.now().strftime("%Y-%m-%d") + "%"
            rows = store.query("SELECT kind, peak_level FROM hazards WHERE started_at LIKE ?", (day,))
            self._today = {"t": time.time(), "data": {
                "camel": sum(1 for r in rows if "camel" in (r["kind"] or "")),
                "danger": sum(1 for r in rows if r["peak_level"] == "DANGER"),
                "vehicles": store.scalar("SELECT COUNT(*) FROM vehicles WHERE ts LIKE ?", (day,)),
                "overspeed": store.scalar("SELECT COUNT(*) FROM vehicles WHERE ts LIKE ? AND overspeed=1", (day,)),
            }}
        return self._today["data"]

    def watchdog(self):
        while True:
            time.sleep(1)
            changed = False
            with self.lock:
                for d in self.devs.values():
                    if d.online and time.time() - d.last_seen > OFFLINE_AFTER_S:
                        d.online = False
                        d.state = None      # re-evaluate transitions when it comes back
                        if d.open_hazard is not None:
                            self.close_hazard(d)
                        self.log_event("warning", "Device offline" + (f" — {d.location}" if d.location else ""),
                                       f"No data for more than {OFFLINE_AFTER_S} seconds", d.name)
                        changed = True
            if changed:
                self.notify()

    def payload(self):
        return {
            "devices": [d.view() for d in self.sorted_devs()],
            "alert": self.alert,
            "today": self.today(),
            "server": {"ip": lan_ip(), "port": PORT, "discovery": DISCOVERY_PORT},
            "serverTime": now_str(),
        }


# ---------------------------------------------------------------- firmware jobs & system info
class Job:
    """One background job at a time (compile / upload / check / install) with a line log."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state, self.kind, self.lines, self.result = "idle", "", [], None
        self.started = self.ended = None

    def start(self, kind, fn):
        with self.lock:
            if self.state == "running":
                return False
            self.state, self.kind, self.lines, self.result = "running", kind, [], None
            self.started, self.ended = now_str(), None
        threading.Thread(target=self._run, args=(fn,), daemon=True).start()
        return True

    def log(self, line):
        with self.lock:
            self.lines.append(line)

    def _run(self, fn):
        try:
            res = fn(self.log) or {}
        except Exception as e:  # never let a tool error kill the dashboard
            self.log(f"[error] {e}")
            res = {"ok": False}
        with self.lock:
            self.state = "done" if res.get("ok") else "failed"
            self.result = {k: v for k, v in res.items() if k != "lines"}
            self.ended = now_str()

    def snapshot(self, since=0):
        with self.lock:
            return {"state": self.state, "kind": self.kind, "started": self.started, "ended": self.ended,
                    "result": self.result, "lines": self.lines[since:], "next": len(self.lines)}


job = Job()
_env_cache = {"t": 0, "data": None}
discovery_state = {"ok": False, "error": ""}


def tools_env(refresh=False):
    if refresh or not _env_cache["data"] or time.time() - _env_cache["t"] > 300:
        _env_cache.update(t=time.time(), data=firmware_tool.environment())
    return _env_cache["data"]


def system_info(refresh=False):
    saved = project_settings.load()
    last = project_settings.last_upload()
    return {
        "computer": project_settings.computer(),
        "running": {"dashboard": PORT, "discovery": DISCOVERY_PORT, "discoveryOk": discovery_state["ok"],
                    "discoveryError": discovery_state["error"]},
        "saved": saved,
        "restartNeeded": saved["dashboard"] != PORT or saved["discovery"] != DISCOVERY_PORT,
        "firmwareNeeded": bool(last.get("discovery")) and last["discovery"] != saved["discovery"],
        "supervised": SUPERVISED,
        "firewall": project_settings.firewall_status(saved["dashboard"], saved["discovery"]),
        "configH": project_settings.config_h_ports(),
        "sketchFw": project_settings.sketch_version(),
        "devices": [{"key": d.key, "name": d.name, "location": d.location, "online": d.online,
                     "mac": d.meta.get("mac") or "", "fw": (d.latest or {}).get("fw")} for d in hub.sorted_devs()],
        "lastUpload": last,
        "tools": tools_env(refresh),
        "comPorts": firmware_tool.list_ports(),
        "boards": project_settings.boards(),       # {COMx: MAC} seen on this PC
        "job": job.snapshot(since=10 ** 9),
    }


def firmware_job(action, port):
    def run(log):
        if action == "compile":
            return firmware_tool.compile_sketch(log)
        if action == "check":
            res = firmware_tool.check_device(port, log)
            project_settings.record_board(port, (res.get("info") or {}).get("mac"))
            return res
        if action == "install":
            res = {"ok": firmware_tool.install_missing(log)}
            tools_env(refresh=True)
            return res
        res = firmware_tool.upload(port, log)
        if res.get("ok"):
            disc = project_settings.config_h_ports().get("discovery")
            project_settings.record_upload(res.get("version"), disc, port)
            info = (res.get("check") or {}).get("info") or {}
            project_settings.record_board(port, info.get("mac"))
            hub.log_event("info", "Firmware uploaded", f"v{res.get('version')} via {port} (discovery UDP {disc})"
                          + (f" — board {info.get('name')} {info.get('mac', '')}" if info else ""), info.get("name", ""))
        else:
            hub.log_event("warning", "Firmware upload failed", f"via {port}")
        return res
    return run


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_request(self, code="-", size="-"):  # the dashboard polls constantly: only log failures
        if isinstance(code, int) and code >= 400:
            super().log_request(code, size)

    def send_json(self, obj, code=200):
        # Raw UTF-8, not \uXXXX: the firmware's small JSON reader does not decode \u escapes,
        # so a WiFi name like "Café" would otherwise be stored as "Cafu00e9".
        # Compact separators too: the firmware looks for keys like "nets":[ without spaces.
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_csv(self, rows, header, filename):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        body = buf.getvalue().encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return None

    # ---- login (cloud mode)
    def authorized(self, path):
        """False (and a 401 already sent) when this request lacks the password / device key."""
        if path == "/healthz":
            return True
        if path == "/api/telemetry":
            if not DEVICE_KEY or hmac.compare_digest(self.headers.get("X-Device-Key", ""), DEVICE_KEY):
                return True
            self.send_json({"error": "wrong or missing X-Device-Key"}, 401)
            return False
        if not DASHBOARD_PASSWORD:
            return True
        given = ""
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                given = base64.b64decode(auth[6:]).decode("utf-8").partition(":")[2]
            except (ValueError, UnicodeDecodeError):
                given = ""
        if given and hmac.compare_digest(given, DASHBOARD_PASSWORD):
            return True
        body = b"Login required"
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Camel Guard", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    # ---- routing
    def do_GET(self):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        if not self.authorized(path):
            return
        if path == "/healthz":
            return self.send_json({"ok": True})
        if path in ("/", "/index.html"):
            self.serve_file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
        elif path == "/api/version":
            disk = str(int(os.path.getmtime(os.path.abspath(__file__))))
            self.send_json({"build": SERVER_BUILD, "disk": disk, "stale": disk != SERVER_BUILD, "pid": os.getpid()})
        elif path == "/api/team":
            self.send_json({**team_list(), "editable": self.is_local(), "limits": TEAM_LIMITS})
        elif path.startswith("/team-photo/"):
            name = path.rsplit("/", 1)[-1]
            ext = name.rsplit(".", 1)[-1].lower()
            if not re.fullmatch(r"\d+-\d+\.(jpg|png|webp)", name):
                return self.send_json({"error": "not found"}, 404)
            self.serve_file(os.path.join(PHOTO_DIR, name), {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[ext])
        elif path == "/wiring.svg":
            self.serve_file(WIRING_SVG, "image/svg+xml; charset=utf-8")
        elif path == "/api/state":
            dev = hub.resolve((q.get("device") or [""])[0])
            self.send_json({**hub.payload(), "chart": list(dev.history) if dev else []})
        elif path == "/api/stream":
            self.stream()
        elif path == "/api/devices":
            self.send_json(hub.payload()["devices"])
        elif path == "/api/settings":
            dev = hub.resolve((q.get("device") or [""])[0])
            if not dev:
                return self.send_json({"error": "choose a device (?device=<key>)"}, 400)
            self.send_json({**dev.view(), "limits": CONFIG_LIMITS,
                            "server": {"ip": lan_ip(), "port": PORT, "discovery": DISCOVERY_PORT}})
        elif path == "/api/logs":
            self.send_json(self.logs(q))
        elif path == "/api/logs.csv":
            rows, _ = query_logs(q, limit=None)
            self.send_csv([[r["ts"], r["level"], r["message"], r["detail"] or "", r["device"] or ""] for r in rows],
                          ["Time", "Type", "Event", "Details", "Device"],
                          f"road_safety_log_{datetime.now():%Y%m%d}.csv")
        elif path == "/api/history":
            self.send_json(store.query("SELECT * FROM hazards ORDER BY id DESC LIMIT 500"))
        elif path == "/api/history.csv":
            rows = store.query("SELECT * FROM hazards ORDER BY id")
            self.send_csv([[r["id"], r["device"], r.get("location") or "", r["started_at"], r["ended_at"] or "",
                            r["kind"], r["peak_level"], r["vehicles"]] for r in rows],
                          ["No", "Crossing", "Location", "Started", "Ended", "Hazard", "Peak Level", "Vehicles"],
                          f"hazard_history_{datetime.now():%Y%m%d}.csv")
        elif path == "/api/vehicles":
            dev = (q.get("device") or [""])[0]
            try:
                limit = max(1, min(500, int((q.get("limit") or ["20"])[0])))
            except ValueError:
                limit = 20
            if dev:
                rows = store.query("SELECT * FROM vehicles WHERE device_key=? ORDER BY id DESC LIMIT ?", (dev, limit))
            else:
                rows = store.query("SELECT * FROM vehicles ORDER BY id DESC LIMIT ?", (limit,))
            self.send_json(rows)
        elif path == "/api/vehicles.csv":
            rows = store.query("SELECT * FROM vehicles ORDER BY id")
            self.send_csv([[r["id"], r["ts"], r["device"], r.get("location") or "", r["dir"], r["state"]]
                           for r in rows],
                          ["No", "Time", "Crossing", "Location", "From Side", "Road State"],
                          f"vehicles_{datetime.now():%Y%m%d}.csv")
        elif path == "/api/system":
            if not self.is_local():
                return self.send_json({"local": False, "running": {"dashboard": PORT}})
            self.send_json({"local": True, **system_info(refresh=q.get("refresh") == ["1"])})
        elif path == "/api/firmware/job":
            if self.system_denied(post=False):
                return
            try:
                since = max(0, int((q.get("since") or ["0"])[0]))
            except ValueError:
                since = 0
            self.send_json(job.snapshot(since))
        else:
            self.send_json({"error": "not found"}, 404)

    # ---- system endpoints: this PC only, and only from the dashboard page
    def is_local(self):
        ip = self.client_address[0]
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1") and host in ("localhost", "127.0.0.1", "::1")

    def system_denied(self, post):
        """Blocks other PCs on the WiFi, and (via the custom header) other web pages in the browser:
        a cross-site request cannot add X-CamelGuard without a CORS preflight, which we never answer."""
        if not self.is_local():
            self.send_json({"error": f"Only available on the PC running the dashboard: open http://localhost:{PORT}"}, 403)
            return True
        if post and self.headers.get("X-CamelGuard") != "1":
            self.send_json({"error": "missing X-CamelGuard header"}, 403)
            return True
        return False

    def system_post(self, path, data):
        if path == "/api/system/check-port":
            proto = "udp" if data.get("proto") == "udp" else "tcp"
            port = project_settings.valid_port(data.get("port"))
            running = DISCOVERY_PORT if proto == "udp" else PORT
            if port is not None and port == running:
                return self.send_json({"ok": True, "state": "ours", "text": "used by this dashboard"})
            return self.send_json(project_settings.check_port(data.get("port"), proto, own_pid=os.getpid()))

        if path == "/api/system/ports":
            saved = project_settings.load()
            want = {"dashboard": data.get("dashboard", saved["dashboard"]),
                    "discovery": data.get("discovery", saved["discovery"])}
            errors, ports = {}, {}
            for key, proto, running in (("dashboard", "tcp", PORT), ("discovery", "udp", DISCOVERY_PORT)):
                p = project_settings.valid_port(want[key])
                if p is None:
                    errors[key] = f"use a number between {project_settings.PORT_MIN} and {project_settings.PORT_MAX}"
                    continue
                if p not in (running, saved[key]):
                    chk = project_settings.check_port(p, proto, own_pid=os.getpid())
                    if not chk["ok"]:
                        errors[key] = f"port {p} is {chk['text']}"
                        continue
                ports[key] = p
            com = str(data.get("com", saved["com"]) or "").strip().upper()
            if com and not re.fullmatch(r"COM\d{1,3}", com):
                errors["com"] = "must look like COM7"
            if errors:
                return self.send_json({"ok": False, "errors": errors}, 400)
            new = project_settings.save(ports["dashboard"], ports["discovery"], com)
            changed = project_settings.sync_config_h(new)
            if (new["dashboard"], new["discovery"], new["com"]) != (saved["dashboard"], saved["discovery"], saved["com"]):
                hub.log_event("info", "Ports changed",
                              f"dashboard TCP {new['dashboard']}, discovery UDP {new['discovery']}, "
                              f"USB {new['com'] or '-'} on {project_settings.computer()}")
            return self.send_json({"ok": True, "saved": new, "configH": changed, **{
                k: v for k, v in system_info().items() if k in ("restartNeeded", "firmwareNeeded", "firewall")}})

        if path == "/api/system/firewall":
            saved = project_settings.load()
            fw = project_settings.firewall_apply(saved["dashboard"], saved["discovery"])
            ok = fw["tcp"] and fw["udp"]
            hub.log_event("info" if ok else "warning", "Firewall rule updated" if ok else "Firewall rule not updated",
                          f"TCP {saved['dashboard']} + UDP {saved['discovery']}" if ok else "admin prompt declined?")
            return self.send_json({"ok": ok, "firewall": fw})

        if path == "/api/system/restart":
            saved = project_settings.load()
            if not SUPERVISED:
                return self.send_json({"ok": False, "error": "This dashboard was not started with START_DASHBOARD.bat, "
                                       "so it cannot restart itself. Close its window and start it again."}, 409)
            if job.snapshot(10 ** 9)["state"] == "running":
                return self.send_json({"ok": False, "error": "wait for the firmware job to finish"}, 409)
            hub.log_event("info", "Dashboard restarting", f"on TCP {saved['dashboard']} / UDP {saved['discovery']}")
            threading.Timer(0.8, lambda: os._exit(RESTART_CODE)).start()
            return self.send_json({"ok": True, "port": saved["dashboard"]})

        if path == "/api/firmware":
            action = str(data.get("action", ""))
            if action not in ("compile", "upload", "check", "install"):
                return self.send_json({"error": "unknown action"}, 400)
            port = str(data.get("com") or project_settings.load()["com"] or "").strip().upper()
            if action in ("upload", "check"):
                if not re.fullmatch(r"COM\d{1,3}", port):
                    return self.send_json({"error": "choose the ESP32's USB port first"}, 400)
                if port != project_settings.load()["com"]:
                    project_settings.save(com=port)
            if not job.start(action, firmware_job(action, port)):
                return self.send_json({"error": "another firmware job is still running"}, 409)
            return self.send_json({"ok": True})

        return self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if not self.authorized(path):
            return
        data = self.read_json()
        if data is None:
            return self.send_json({"error": "invalid json"}, 400)

        if path == "/api/telemetry":
            return self.send_json(hub.on_telemetry(data))

        if path.startswith("/api/system/") or path == "/api/firmware":
            if self.system_denied(post=True):
                return
            return self.system_post(path, data)

        if path in ("/api/team/save", "/api/team/delete"):
            if self.system_denied(post=True):
                return
            res, code = team_save(data) if path == "/api/team/save" else team_delete(data)
            return self.send_json(res, code)

        if path == "/api/alert":
            return self.send_json({"ok": True, **hub.set_alert(bool(data.get("on")))})

        if path == "/api/reset" and data.get("all"):
            return self.send_json({"ok": True, "crossings": hub.reset_all()})

        # Everything below acts on ONE device. With several devices the request must say which
        # one — otherwise a CLEAR or a test could reach the wrong crossing.
        if path in ("/api/reset", "/api/test", "/api/command", "/api/config", "/api/wifi",
                    "/api/devices/update", "/api/devices/forget"):
            dev = hub.resolve(str(data.get("device") or ""))
            if not dev:
                return self.send_json({"error": "choose a device first" if hub.devs else "no device yet"}, 400)
            return self.device_post(path, data, dev)

        self.send_json({"error": "not found"}, 404)

    def device_post(self, path, data, dev):
        if path == "/api/reset":
            return self.send_json({"ok": True, "id": hub.request_reset(dev)})

        if path == "/api/test":
            out = str(data.get("out", ""))
            if out not in TEST_OUTPUTS and out != "none":
                return self.send_json({"error": "unknown output"}, 400)
            if not dev.online:
                return self.send_json({"error": f"{dev.name} is offline - a test would run later, unattended"}, 409)
            st = dev.latest or {}
            if out != "none" and st.get("state") in HAZARD_STATES:
                return self.send_json({"error": "a hazard is active - tests are blocked"}, 409)
            try:
                sec = max(1, min(TEST_MAX_S, int(data.get("sec", 5))))
            except (TypeError, ValueError):
                sec = 5
            label = f"Output test: {TEST_OUTPUTS[out]} ({sec} s)" if out != "none" else "Output test stopped"
            hub.log_event("info", label, "requested from the dashboard", dev.name)
            return self.send_json({"ok": True, "sec": sec, "id": hub.enqueue(
                dev, {"cmd": "test", "out": out, "sec": sec}, label, ttl=TEST_TTL_S, urgent=True)})

        if path == "/api/command":
            cmd = str(data.get("cmd", ""))
            labels = {"reboot": "Reboot device", "factory": "Factory reset (device settings)"}
            if cmd not in labels:
                return self.send_json({"error": "unknown command"}, 400)
            hub.log_event("info", labels[cmd], "requested from the dashboard", dev.name)
            return self.send_json({"ok": True, "id": hub.enqueue(dev, {"cmd": cmd}, labels[cmd])})

        if path == "/api/config":
            cfg = clean_config(data.get("cfg") or {})
            if not cfg:
                return self.send_json({"error": "nothing to change"}, 400)
            changed = ", ".join(f"{k}={v}" for k, v in sorted(cfg.items()))
            return self.send_json({"ok": True, "applied": cfg,
                                   "id": hub.enqueue(dev, {"cmd": "config", "cfg": cfg}, changed)})

        if path == "/api/wifi":
            return self.wifi(data, dev)

        if path == "/api/devices/update":
            out = {"ok": True}
            if "location" in data:
                loc = re.sub(r"[\x00-\x1f<>]", "", str(data.get("location") or "")).strip()[:40]
                hub.update_meta(dev, location=loc)
                hub.log_event("info", "Device location set", f"{dev.name}: {loc or '(none)'}", dev.name)
            if data.get("name"):   # the name lives on the device itself (NVS) — send it there
                cfg = clean_config({"name": data["name"]})
                if cfg:
                    out["id"] = hub.enqueue(dev, {"cmd": "config", "cfg": cfg}, f"name={cfg['name']}")
            return self.send_json(out)

        if path == "/api/devices/forget":
            if dev.online:
                return self.send_json({"error": "the device is online — it would reappear at once"}, 409)
            hub.forget(dev)
            return self.send_json({"ok": True})

    # ---- handlers
    def wifi(self, data, dev):
        nets = []
        for n in (data.get("nets") or [])[:MAX_WIFI_NETWORKS]:
            ssid = str(n.get("ssid", "")).strip()[:32]
            pw = str(n.get("pass", ""))[:63]
            if not ssid:
                continue
            # An entry sent without a password keeps the one already stored on the device.
            nets.append({"ssid": ssid, "pass": pw, "keep": bool(n.get("keep")) or pw == ""})
        if not nets:
            return self.send_json({"error": "at least one network is required"}, 400)

        body = {"cmd": "wifi", "nets": nets}
        host = str(data.get("host", "")).strip()
        # "name:port" sets the port too (a cloud dashboard: "xxx.onrender.com:443" = HTTPS); a pasted URL is trimmed
        host = re.sub(r"^https?://", "", host).rstrip("/")
        port_in_host = re.fullmatch(r"(.+):(\d{1,5})", host)
        if port_in_host:
            host, data["port"] = port_in_host.group(1), port_in_host.group(2)
        host = host[:40]
        if host:
            body["host"] = host
            try:
                body["port"] = max(1, min(65535, int(data.get("port", PORT))))
            except (TypeError, ValueError):
                body["port"] = PORT
        label = "WiFi list: " + ", ".join(n["ssid"] for n in nets) + (f" · dashboard {host}" if host else "")
        # The log must never contain the passwords.
        hub.log_event("info", "WiFi settings sent to the device", label, dev.name)
        return self.send_json({"ok": True, "id": hub.enqueue(dev, body, label),
                               "nets": [n["ssid"] for n in nets]})

    def logs(self, q):
        try:
            page = max(1, int(q.get("page", ["1"])[0]))
            size = max(5, min(100, int(q.get("size", ["10"])[0])))
        except ValueError:
            page, size = 1, 10
        rows, total = query_logs(q, limit=size, offset=(page - 1) * size)
        where, args = log_filter(q, with_level=False)
        summary = {lvl: store.scalar(f"SELECT COUNT(*) FROM events WHERE {where} AND level=?", args + [lvl])
                   for lvl in ("alarm", "warning", "info")}
        return {"rows": rows, "total": total, "page": page, "size": size,
                "pages": max(1, -(-total // size)), "summary": summary,
                "devices": [r["device"] for r in store.query(
                    "SELECT DISTINCT device FROM events WHERE device <> '' ORDER BY device")]}

    def serve_file(self, fpath, ctype):
        try:
            with open(fpath, "rb") as fh:
                body = fh.read()
        except FileNotFoundError:
            return self.send_json({"error": "file not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        seen = -1
        try:
            while True:
                with hub.cond:
                    if hub.version == seen:
                        hub.cond.wait(timeout=15)
                    changed = hub.version != seen
                    seen = hub.version
                msg = f"data: {json.dumps(hub.payload())}\n\n" if changed else ": keep-alive\n\n"
                self.wfile.write(msg.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass


# ---------------------------------------------------------------- log queries
def log_filter(q, with_level=True):
    """Builds the WHERE clause shared by the log list, the CSV export and the summary."""
    where, args = ["1=1"], []
    frm = (q.get("from") or [""])[0].strip()
    to = (q.get("to") or [""])[0].strip()
    level = (q.get("level") or [""])[0].strip()
    device = (q.get("device") or [""])[0].strip()
    search = (q.get("q") or [""])[0].strip()
    if frm:
        where.append("ts >= ?"); args.append(frm + " 00:00:00")
    if to:
        where.append("ts <= ?"); args.append(to + " 23:59:59")
    if device:
        where.append("device = ?"); args.append(device)
    if search:
        where.append("(message LIKE ? OR detail LIKE ?)"); args += [f"%{search}%"] * 2
    if with_level and level in ("alarm", "warning", "info"):
        where.append("level = ?"); args.append(level)
    return " AND ".join(where), args


# ---------------------------------------------------------------- project team (Team page)
def team_list():
    rows = store.query("SELECT * FROM team ORDER BY sort, id")
    for r in rows:
        r["photoUrl"] = f"/team-photo/{r['photo']}" if r.get("photo") else ""
    return {"authors": [r for r in rows if r["role"] == "author"],
            "supervisors": [r for r in rows if r["role"] == "supervisor"]}


def _photo_bytes(data_url):
    """data:image/...;base64,... -> (bytes, ext) after checking the file really is an image."""
    import base64
    m = re.match(r"data:image/(jpeg|jpg|png|webp);base64,(.+)$", data_url or "", re.S)
    if not m:
        return None, "the photo must be a JPEG, PNG or WebP image"
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        return None, "the photo could not be read"
    if len(raw) > PHOTO_MAX_BYTES:
        return None, "the photo is larger than 2 MB"
    if raw[:3] == b"\xff\xd8\xff":
        return raw, "jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return raw, "png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return raw, "webp"
    return None, "the file is not a JPEG, PNG or WebP image"


def _drop_photo(name):
    if name:
        try:
            os.remove(os.path.join(PHOTO_DIR, name))
        except OSError:
            pass


def team_save(data):
    role = data.get("role")
    if role not in TEAM_LIMITS:
        return {"error": "role must be author or supervisor"}, 400
    name = re.sub(r"\s+", " ", str(data.get("name") or "")).strip()[:60]
    idno = str(data.get("idno") or "").strip()[:30]
    email = str(data.get("email") or "").strip()[:80]
    if not name:
        return {"error": "the name is required"}, 400
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return {"error": "that e-mail address does not look right"}, 400
    pid = data.get("id")
    old = store.query("SELECT * FROM team WHERE id=?", (pid,)) if pid else []
    if pid and not old:
        return {"error": "that person no longer exists"}, 404
    if not old or old[0]["role"] != role:
        count = store.scalar("SELECT COUNT(*) FROM team WHERE role=?", (role,))
        if count >= TEAM_LIMITS[role][1]:
            return {"error": f"at most {TEAM_LIMITS[role][1]} {role}s"}, 409
        if old and old[0]["role"] != role:
            left = store.scalar("SELECT COUNT(*) FROM team WHERE role=?", (old[0]["role"],))
            if left - 1 < TEAM_LIMITS[old[0]["role"]][0]:
                return {"error": f"at least {TEAM_LIMITS[old[0]['role']][0]} {old[0]['role']} must remain"}, 409
    photo = old[0]["photo"] if old else ""
    new_photo = None
    if data.get("photo"):
        raw, ext = _photo_bytes(data["photo"])
        if raw is None:
            return {"error": ext}, 400
        new_photo = (raw, ext)
    if old:
        store.execute("UPDATE team SET role=?, name=?, idno=?, email=? WHERE id=?", (role, name, idno, email, pid))
    else:
        sort = store.scalar("SELECT COALESCE(MAX(sort), 0) + 1 FROM team")
        pid = store.execute("INSERT INTO team(role, name, idno, email, photo, sort) VALUES (?,?,?,?,?,?)",
                            (role, name, idno, email, "", sort))
    if new_photo or data.get("removePhoto"):
        _drop_photo(photo)
        photo = ""
        if new_photo:
            os.makedirs(PHOTO_DIR, exist_ok=True)
            photo = f"{int(pid)}-{int(time.time())}.{new_photo[1]}"   # new name = no stale browser cache
            with open(os.path.join(PHOTO_DIR, photo), "wb") as fh:
                fh.write(new_photo[0])
        store.execute("UPDATE team SET photo=? WHERE id=?", (photo, pid))
    return {"ok": True, "id": pid, **team_list()}, 200


def team_delete(data):
    rows = store.query("SELECT * FROM team WHERE id=?", (data.get("id"),))
    if not rows:
        return {"error": "that person no longer exists"}, 404
    p = rows[0]
    left = store.scalar("SELECT COUNT(*) FROM team WHERE role=?", (p["role"],))
    if left - 1 < TEAM_LIMITS[p["role"]][0]:
        return {"error": f"at least {TEAM_LIMITS[p['role']][0]} {p['role']} is required — edit it instead"}, 409
    store.execute("DELETE FROM team WHERE id=?", (p["id"],))
    _drop_photo(p["photo"])
    return {"ok": True, **team_list()}, 200


def seed_team():
    """First start only: the team from the project proposal, so the Team page is not empty."""
    if store.scalar("SELECT COUNT(*) FROM team"):
        return
    people = [("supervisor", "Haitham Zein"), ("author", "Mohamed Mubarak Almansoori"),
              ("author", "Mohammed Jumaa"), ("author", "Mansoor Abdulla"), ("author", "Ibrahim Jumaa")]
    for i, (role, name) in enumerate(people, 1):
        store.execute("INSERT INTO team(role, name, idno, email, photo, sort) VALUES (?,?,?,?,?,?)",
                      (role, name, "", "", "", i))


def query_logs(q, limit=10, offset=0):
    where, args = log_filter(q)
    total = store.scalar(f"SELECT COUNT(*) FROM events WHERE {where}", args)
    sql = f"SELECT * FROM events WHERE {where} ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args = args + [limit, offset]
    return store.query(sql, args), total


# ---------------------------------------------------------------- networking
def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ip_toward(addr):
    """This PC's address on the network that reaches `addr` (WiFi, not e.g. a second Ethernet adapter)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((addr, 1))
        return s.getsockname()[0]
    except OSError:
        return lan_ip()
    finally:
        s.close()


def discovery_responder():
    """Answers the ESP32's UDP broadcast so it can find this laptop after a DHCP change."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # No SO_REUSEADDR on Windows: it would let us share the port silently with another program.
    if os.name != "nt":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", DISCOVERY_PORT))
    except OSError as e:
        owner = project_settings.port_owner(DISCOVERY_PORT, "udp")
        who = f"{owner['name']} (pid {owner['pid']})" if owner else "another program"
        discovery_state.update(ok=False, error=f"UDP {DISCOVERY_PORT} is in use by {who}")
        print(f"  !! UDP discovery on port {DISCOVERY_PORT} unavailable ({who}): {e}")
        print("     Choose another discovery port in Settings > Firmware & ports.")
        return
    discovery_state.update(ok=True, error="")
    while True:
        try:
            data, addr = sock.recvfrom(128)
        except OSError:
            continue
        if data.strip().startswith(b"CAMELGUARD_DISCOVER"):
            sock.sendto(f"CAMELGUARD_HERE:{ip_toward(addr[0])}:{PORT}".encode(), addr)


hub = Hub()
seed_team()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second server silently share the port; disable it there
    allow_reuse_address = os.name != "nt"

    def handle_error(self, request, client_address):
        # A browser closing a keep-alive connection (page reload, tab closed) is normal:
        # do not print a traceback into the dashboard window for it.
        if isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def running_build(port):
    """SERVER_BUILD of the dashboard already listening on `port`; '' for a version without /api/version."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2) as r:
            return str(json.loads(r.read()).get("build", ""))
    except Exception:
        return ""


if __name__ == "__main__":
    import argparse
    import webbrowser

    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="open the dashboard in the default browser")
    args = ap.parse_args()
    url = f"http://localhost:{PORT}"

    def bind(tries):
        for attempt in range(tries):   # after a restart the old process may need a moment to release the port
            try:
                return Server(("0.0.0.0", PORT), Handler)
            except OSError:
                if attempt < tries - 1:
                    time.sleep(0.5)
        return None

    if CLOUD and not DASHBOARD_PASSWORD:
        print("ERROR: cloud mode (RENDER / CG_CLOUD) needs the DASHBOARD_PASSWORD environment variable -")
        print("       the dashboard would otherwise be open to everyone on the internet. Set it in the host's settings.")
        raise SystemExit(1)
    server = bind(10 if SUPERVISED else 1)
    owner = project_settings.port_owner(PORT, "tcp") if server is None else None
    if server is None and owner and owner["name"].lower().startswith("python"):
        running = running_build(PORT)
        if running == SERVER_BUILD:
            print(f"The dashboard is already running in another window (pid {owner['pid']}) - opening it.")
            if args.open:
                webbrowser.open(url)
            raise SystemExit(0)
        print("=" * 62)
        print(f" An OLDER dashboard is still running (python, pid {owner['pid']}).")
        print(" It does not know the newest features (the page would show 'not found' errors).")
        print("=" * 62)
        answer = ""
        if sys.stdin.isatty():
            try:
                answer = input(" Stop the old one and start this version? [Y/n] ").strip().lower()
            except EOFError:
                answer = "n"
        if sys.stdin.isatty() and answer in ("", "y", "yes"):
            import subprocess
            subprocess.run(["taskkill", "/PID", str(owner["pid"]), "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(" Old dashboard stopped (its window now says 'Press any key' - you can close it).")
            server = bind(20)
        else:
            print(" Close the old dashboard window, then run START_DASHBOARD.bat again.")
            raise SystemExit(0)
    if server is None:
        owner = owner or project_settings.port_owner(PORT, "tcp")
        if owner and owner["name"].lower().startswith("python"):
            print(f"Port {PORT} is still in use by python (pid {owner['pid']}). Close that window and try again.")
        else:
            who = f"{owner['name']} (pid {owner['pid']})" if owner else "another program"
            print(f"Port {PORT} is in use by {who}. Choose another dashboard port in camel_guard.ini")
            print("or with FIRMWARE_TOOLS.bat (menu 4), then start the dashboard again.")
        raise SystemExit(0)

    threading.Thread(target=hub.watchdog, daemon=True).start()
    if CLOUD:   # no LAN to discover over the internet: the ESP32 gets the address by hand (docs/DEPLOY_CLOUD.id.md)
        print(f"Camel Guard dashboard (cloud mode) on port {PORT} - login required"
              + ("" if DEVICE_KEY else " · WARNING: DEVICE_KEY not set, anyone can post telemetry"))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        raise SystemExit(0)
    threading.Thread(target=discovery_responder, daemon=True).start()
    ip = lan_ip()
    print("=" * 62)
    print(" Camel Guard - Road Safety Dashboard (miniature simulator)")
    print(f"  Dashboard : {url}  or  http://{ip}:{PORT}")
    print(f"  ESP32     : posts to http://{ip}:{PORT}/api/telemetry")
    print(f"  Discovery : UDP {DISCOVERY_PORT} (the device finds this PC even if the IP changes)")
    print(f"  Ports     : change them in Settings > Firmware & ports (saved in camel_guard.ini)")
    print("  Waiting for the ESP32... (Ctrl+C to stop)")
    print("=" * 62)
    if args.open:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Server stopped.")
