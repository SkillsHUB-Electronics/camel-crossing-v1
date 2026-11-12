"""
ESP32 simulator — test the dashboard without hardware.
Detection logic (camel gate: IR A / IR B alone confirms (no PIR), direction from the first IR, hold
time; vehicle HC-SR04 at the 400 m point of each approach, pinging across the lane: median of 3, <= carCm = a car
passing; that side's signs flash fast for approachSec), state machine, outputs (one yellow lamp always flashing, signs paralleled), settings
and the command channel are identical to the firmware (2.3.1), so every dashboard page can be tested.
A solar panel + battery with two INA219 is simulated too (a 4-minute day / night cycle, 30x faster battery).

  python simulator.py                        # one camel crossing, auto tour of every situation
  python simulator.py --scenario camel       # a camel crosses from side A, later one from side B (every ~23 s)
  python simulator.py --scenario danger      # a camel crosses while cars keep coming
  python simulator.py --scenario multi       # three crossings with different traffic
  python simulator.py --devices 4            # four crossings; crossing 1 runs --scenario
  python simulator.py --host 192.168.1.10    # server on another PC

Each simulated crossing has its own MAC address (5A:17:00:00:01:0n — a locally administered
range no real ESP32 uses), so the dashboard treats them exactly like separate boards.
"""
import argparse
import json
import math
import random
import time
import urllib.request

# --- same defaults as firmware/config.h + sensors_config.h; the dashboard may change these at runtime ---
DEFAULT_CFG = {
    "name": "esp32-simulator", "carCm": 15, "confirm": 3, "holdSec": 5, "approachSec": 8,
    "useA": True, "useB": True, "buzzer": True,
}
DEFAULT_NETS = ["Experts_Only"]   # what config.h would supply on a factory-fresh device
# Limits, mirroring applyConfig() in the firmware
LIMITS = {"carCm": (3, 150), "confirm": (1, 20), "holdSec": (2, 60), "approachSec": (1, 20)}
FLAGS = ("useA", "useB", "buzzer")
SCENARIOS = ["auto", "clear", "camel", "danger", "multi"]
WARMUP_S = 3.0          # WARMUP_MS in config.h
VEH_DEBOUNCE_S = 0.3    # VEH_DEBOUNCE_MS in sensors_config.h
CAR_NEAR_CM = (4.0, 10.0)   # vehicle HC-SR04 beside the lane at the 400 m point: a passing toy car (empty lane = nothing in range)
US_SAMPLES = 3          # CAR_US_SAMPLES (median)
FAULT_READINGS = 20     # US_FAULT_READINGS in config.h
STUCK_S = 120.0         # GATE_STUCK_MS in sensors_config.h
# --- power: same limits as config.h
BAT_LOW_V, BAT_OK_V, BAT_FULL_V = 3.45, 3.60, 4.10
CHG_MIN_MA, SUN_MIN_V = 20, 4.5
BAT_CAP_MAH = 900.0     # a small cell, so the charge visibly changes within minutes
POWER_SPEED = 30.0      # battery runs this many times faster than real time
SUN_PERIOD_S = 240.0    # one simulated day
VT = [(3.30, 0), (3.50, 6), (3.60, 12), (3.70, 25), (3.80, 45), (3.90, 65), (4.00, 80), (4.10, 90), (4.20, 100)]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def soc_to_v(soc):
    """Resting voltage of the Li-ion cell for a charge in % (inverse of batPercent())."""
    for (v0, p0), (v1, p1) in zip(VT, VT[1:]):
        if soc <= p1:
            return v0 + (v1 - v0) * (soc - p0) / (p1 - p0)
    return VT[-1][0]


def v_to_pct(v):
    if v <= VT[0][0]:
        return 0
    for (v0, p0), (v1, p1) in zip(VT, VT[1:]):
        if v <= v1:
            return int(p0 + (p1 - p0) * (v - v0) / (v1 - v0) + 0.5)
    return 100


def default_port():
    """Dashboard port from camel_guard.ini, like the dashboard itself."""
    try:
        import os
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
        import project_settings
        return project_settings.load()["dashboard"]
    except Exception:
        return 5100


class World:
    """The model highway: what the camel gate (IR A, IR B) and the two vehicle HC-SR04 see."""

    PLANS = {
        # (situation, seconds). A camel scene lasts 14 s: the camel walks into the gate past IR A (0.5-2.5 s, the
        # legs leave short gaps) and leaves past IR B (5-7 s). "camelB" is the same from side B.
        "auto": [("clear", 12), ("car", 8), ("camel", 14), ("clear", 8), ("clear", 8), ("camelB+car", 14), ("clear", 10),
                 ("camel+car", 14), ("clear", 8)],
        "clear": [("clear", 30), ("car", 6)],
        "camel": [("clear", 12), ("camel", 14), ("clear", 6), ("camelB", 14)],
        "danger": [("clear", 8), ("camel+car", 14)],
    }

    def __init__(self, scenario, log):
        self.log = log
        self.plan = self.PLANS.get(scenario, self.PLANS["auto"])
        self.i, self.t_in = 0, 0.0
        self.sit = self.plan[0][0]
        self.car = None            # (side, seconds left in front of its sensor) of a car passing a vehicle sensor
        self.next_car = 0.0

    def step(self, dt):
        name, dur = self.plan[self.i]
        self.t_in += dt
        if self.t_in >= dur:
            self.i = (self.i + 1) % len(self.plan)
            self.t_in = 0.0
            self.next_car = 0.0
            self.sit = self.plan[self.i][0]
            self.log(f"Scene: {self.sit}")
        s, k = self.sit, self.t_in
        camel = "camel" in s
        first, second = ("B", "A") if "camelB" in s else ("A", "B")
        gap = random.random() < 0.15          # the legs leave short gaps in the IR beam
        ir = {"A": False, "B": False}
        if camel:
            ir[first] = 0.5 < k < 2.5 and not gap
            ir[second] = 5.0 < k < 7.0 and not gap
        # cars: one now and then (about one every 12 s), more when the scene asks for one
        if self.car is None and (("car" in s and k >= self.next_car) or random.random() < 0.008):
            self.car = (random.choice("AB"), random.uniform(0.25, 0.5))
            self.next_car = k + random.uniform(4, 6)
        under = {"A": False, "B": False}
        if self.car:
            side, left = self.car
            under[side] = True
            left -= dt
            self.car = (side, left) if left > 0 else None

        def echo(car):
            """What a vehicle HC-SR04 (beside the lane, pinging across it) reports; None = no echo pulse at all."""
            if random.random() < 0.01:
                return None
            return random.uniform(*CAR_NEAR_CM) if car else math.inf   # empty lane: nothing in range

        return {"irA": ir["A"], "irB": ir["B"], "dA": echo(under["A"]), "dB": echo(under["B"])}


class CarSensor:
    """One vehicle HC-SR04 (CarSensor in the firmware)."""

    def __init__(self, tag):
        self.tag = tag
        self.hist, self.dead_n = [], 0
        self.dist = 0.0            # 0 = unknown, math.inf = nothing in range
        self.car, self.car_since, self.last_hit, self.count = False, 0.0, 0.0, 0


class GateIr:
    """One camel IR sensor at the gate (GateIr in the firmware)."""

    def __init__(self, tag):
        self.tag = tag
        self.raw, self.blocked_n, self.seen, self.seen_t, self.blocked_since = False, 0, False, 0.0, 0.0


class Device:
    """The firmware, as far as the dashboard can see it."""

    def __init__(self, name, mac, url, log, power=True):
        self.log = log
        self.has_power = power          # INA219 modules fitted (battery + solar panel)
        self.soc = 62.0                 # battery charge in %: a battery survives a reboot
        self.mac = mac
        self.url = url
        self.default_name = name
        self.cfg = dict(DEFAULT_CFG, name=name)
        self.nets = ["Experts_Only", "meParaDx"]
        self.boot_state()

    def boot_state(self):
        """Everything that a power cycle resets. Settings and the WiFi list live in NVS on the
        real device, so they deliberately survive here too."""
        self.t = 0.0               # simulated seconds since boot (advanced in 50 ms steps)
        self.ack = 0
        self.last_sent_ack = 0
        self.restart_after_ack, self.restart_mode = 0, ""
        self.alert = False
        self.test, self.test_until, self.test_t0 = "none", 0.0, 0.0
        self.uA, self.uB = CarSensor("A"), CarSensor("B")
        self.gA, self.gB = GateIr("A"), GateIr("B")
        self.camel_on, self.camel_dir, self.camel_seen = False, "", 0.0
        self.camel_count = self.veh_count = 0
        self.last_dir = ""
        self.state, self.hazard, self.hazard_start = "WARMUP", "", 0.0
        self.pv_v = self.pv_ma = self.bat_v = self.bat_ma = 0.0
        self.bat_low = False

    def now(self):
        return self.t

    def power_step(self, dt):
        """Solar panel, load and Li-ion cell: what the two INA219 would report (powerStep() in the firmware)."""
        if not self.has_power:
            return
        sun = max(0.0, math.sin(2 * math.pi * (self.now() / SUN_PERIOD_S + 0.12)))      # day half / night half
        day = sun >= 0.1
        self.pv_v = 4.6 + 1.9 * sun + random.gauss(0, 0.03) if day else 0.2
        self.pv_ma = 340.0 * sun + random.gauss(0, 2) if day else 0.0
        load = 190.0 + (70.0 if self.state in ("WARNING", "DANGER") else 0.0)           # ESP32 + sensors + LEDs (+ buzzer)
        net = self.pv_ma * 0.92 - load                                                  # charger / boost losses
        if self.soc >= 99.5 and net > 0:
            net = random.uniform(-5, 5)                                                 # full: the charger stops
        self.bat_ma = net + random.gauss(0, 3)
        self.soc = clamp(self.soc + self.bat_ma * dt * POWER_SPEED / 3600.0 / BAT_CAP_MAH * 100.0, 0.0, 100.0)
        self.bat_v = soc_to_v(self.soc) + self.bat_ma / 1000.0 * 0.12                  # cell resistance
        if not self.bat_low and self.bat_v < BAT_LOW_V:
            self.bat_low = True
            self.log(f"Battery low: {self.bat_v:.2f} V")
        elif self.bat_low and self.bat_v >= BAT_OK_V:
            self.bat_low = False
            self.log("Battery recovered")

    def power_view(self):
        """Telemetry fields of the power part."""
        if not self.has_power:
            return {"vbat": 0, "pvOk": False, "batOk": False, "sun": False, "pvV": 0, "pvmA": 0, "batV": 0, "batmA": 0,
                    "batPct": -1, "chg": "none"}
        chg = ("charging" if self.bat_ma > CHG_MIN_MA else "discharging" if self.bat_ma < -CHG_MIN_MA
               else "full" if self.bat_v >= BAT_FULL_V else "idle")
        return {"vbat": round(self.bat_v, 2), "pvOk": True, "batOk": True, "sun": self.pv_v >= SUN_MIN_V,
                "pvV": round(self.pv_v, 2), "pvmA": round(self.pv_ma), "batV": round(self.bat_v, 2),
                "batmA": round(self.bat_ma), "batPct": v_to_pct(self.bat_v), "chg": chg}

    def faults(self):
        t, out = self.now(), ""
        for c in (self.uA, self.uB):
            if c.dead_n >= FAULT_READINGS:
                out += f"US-{c.tag} "
            if c.car_since and t - c.car_since > STUCK_S:
                out += f"CAR-{c.tag} "
        for g in (self.gA, self.gB):
            if g.blocked_since and t - g.blocked_since > STUCK_S:
                out += f"OBJ-{g.tag} "
        if self.has_power and self.bat_low:
            out += "BAT-LOW "
        return out

    # ---- sensing: same steps as sampleCar() / sampleGateIr() / sampleGate() / evaluate()
    def sample_car(self, c, cm):
        """cm = what the HC-SR04 reported: a distance, math.inf = nothing in range, None = no echo pulse at all."""
        t = self.now()
        if cm is None:
            c.dead_n = min(255, c.dead_n + 1)
            if c.dead_n >= 3:                                            # 0.15 s without any echo: unknown
                c.dist, c.hist = 0.0, []
        else:
            c.dead_n = 0
            if math.isfinite(cm) and not 2 <= cm <= 200:                 # US_MIN_CM .. US_MAX_CM
                cm = math.inf
            c.hist = (c.hist + [cm])[-US_SAMPLES:]
            c.dist = sorted(c.hist)[len(c.hist) // 2]                    # median
        car = 0 < c.dist <= self.cfg["carCm"]
        if car and not c.car and (not c.last_hit or t - c.last_hit >= VEH_DEBOUNCE_S):
            c.last_hit = t
            c.count += 1
            self.veh_count += 1
            self.last_dir = c.tag
            self.log(f"Vehicle #{self.veh_count} approaching from side {c.tag} ({c.dist:.1f} cm)")
        c.car_since = (c.car_since or t) if car else 0.0
        c.car = car

    def approaching(self, c):
        return c.last_hit > 0 and self.now() - c.last_hit < self.cfg["approachSec"]

    def sample_gate_ir(self, g, raw):
        cfg, t = self.cfg, self.now()
        if not cfg["use" + g.tag]:
            g.raw = g.seen = False
            g.blocked_n, g.blocked_since = 0, 0.0
            return
        g.raw = raw
        if raw:
            g.blocked_n = min(255, g.blocked_n + 1)
            g.blocked_since = g.blocked_since or t
        else:
            g.blocked_n, g.blocked_since = 0, 0.0
        was, g.seen = g.seen, g.blocked_n >= cfg["confirm"]
        if not g.seen:
            return
        g.seen_t = t
        if was:
            return
        if not self.camel_on:     # during a camel: the same camel walking on, not counted again
            self.camel_on, self.camel_dir, self.camel_seen = True, g.tag, t
            self.camel_count += 1
            self.log(f"Camel CONFIRMED (IR {g.tag}), coming from side {g.tag} - #{self.camel_count}")

    def sample_gate(self, ir_a, ir_b):
        """Every 50 ms: both IR sensors, then the hold logic (sampleGate() in the firmware)."""
        t = self.now()
        self.sample_gate_ir(self.gA, ir_a)
        self.sample_gate_ir(self.gB, ir_b)
        if self.camel_on:
            if self.gA.seen or self.gB.seen:
                self.camel_seen = t
            elif t - self.camel_seen > self.cfg["holdSec"]:
                self.camel_on = False
                self.log("Camel gate clear")

    def evaluate(self):
        t = self.now()
        self.veh_a, self.veh_b = self.approaching(self.uA), self.approaching(self.uB)
        camel = self.camel_on
        hazard = camel or self.alert
        self.hazard = "+".join(k for k, on in (("camel", camel), ("manual", self.alert)) if on)
        prev = self.state
        if t < WARMUP_S and not hazard:
            self.state = "WARMUP"
        elif hazard:
            self.state = "DANGER" if self.veh_a or self.veh_b else "WARNING"
        else:
            self.state = "CLEAR"
        if self.state in ("WARNING", "DANGER") and prev not in ("WARNING", "DANGER"):
            self.hazard_start = t
            self.log(f"!! {self.state}: {self.hazard}")

    def outputs(self):
        """Output modes as reported by updateOutputs() in the firmware."""
        hazard, danger = self.state in ("WARNING", "DANGER"), self.state == "DANGER"
        if self.test != "none" and (hazard or time.time() >= self.test_until):
            self.log("Output test " + ("cancelled - a real hazard has priority" if hazard else "finished"))
            self.test = "none"
        cfg = self.cfg
        # yellow lamp: always flashing; the output test "signals" holds it on. Signs: both sides paralleled.
        sig = "on" if self.test == "signals" else "flash"
        sc = sw = "off"
        if self.test == "signs":
            sc = sw = "flash"
        elif hazard:
            sc = sw = "fast" if danger else "flash"
        s_a = s_b = sw
        self.led = -1
        if self.test == "leds":           # one output at a time, 1 s each: Q0..Q4
            self.led = int(time.time() - self.test_t0) % 5
            sig, sc, sw, s_a, s_b = "off", "off", "off", "off", "off"
        bz = "fast" if danger else "off"
        if not cfg["buzzer"]:
            bz = "muted"
        if self.test == "buzzer":
            bz = "test"
        return sig, sc, sw, s_a, s_b, bz

    # ---- commands: same as handleCommand()
    def handle(self, resp):
        cmd = resp.get("cmd", "none")
        cid = resp.get("id", 0)
        if cmd == "none" or (cid and cid == self.ack):
            return
        if cmd == "reset":
            self.test = "none"
            self.alert = False
            self.camel_on = False
            self.gA.blocked_n = self.gB.blocked_n = 0
            self.gA.seen = self.gB.seen = False
            self.uA.last_hit = self.uB.last_hit = 0.0
            self.log("CLEAR from the dashboard")
        elif cmd == "config":
            for k, v in (resp.get("cfg") or {}).items():
                if k == "name":
                    self.cfg["name"] = str(v)[:24]
                elif k in FLAGS:
                    self.cfg[k] = bool(v)
                elif k in LIMITS:
                    lo, hi = LIMITS[k]
                    self.cfg[k] = int(clamp(float(v), lo, hi))
            self.log(f"Settings updated: {resp.get('cfg')}")
        elif cmd == "wifi":
            self.nets = [n.get("ssid", "") for n in (resp.get("nets") or []) if n.get("ssid")][:5]
            self.log(f"WiFi list saved: {', '.join(self.nets)}")
        elif cmd == "alert":
            self.alert = bool(resp.get("on", True))
            self.log(f"Manual alert {'STARTED' if self.alert else 'stopped'} from the dashboard")
        elif cmd == "test":
            out = resp.get("out", "none")
            sec = max(1, min(30, int(resp.get("sec", 5) or 5)))
            if out in ("buzzer", "signals", "signs", "leds"):
                if self.state in ("WARNING", "DANGER"):
                    self.log(f"Output test {out} refused - hazard active")
                else:
                    self.test, self.test_until, self.test_t0 = out, time.time() + sec, time.time()
                    self.log(f"Output test: {out} for {sec} s")
            elif self.test != "none":
                self.test = "none"
                self.log("Output test stopped from the dashboard")
        elif cmd in ("reboot", "factory"):
            # Deferred until the server has seen the ack, exactly like the firmware:
            # restarting first would make the server resend the command forever.
            self.restart_after_ack, self.restart_mode = cid, cmd
            self.log(f"{cmd.capitalize()} requested - waiting for the ack to be delivered")
        else:
            self.log(f"Unknown command {cmd!r} ignored")
            return
        self.ack = cid

    def restart(self):
        if self.restart_mode == "factory":
            self.log("Factory reset - back to the config.h defaults")
            self.cfg = dict(DEFAULT_CFG, name=self.default_name)
            self.nets = list(DEFAULT_NETS)
        else:
            self.log("Rebooting - settings kept (they live in NVS)")
        self.boot_state()

    def telemetry(self, host):
        sig, sc, sw, s_a, s_b, bz = self.outputs()
        t = self.now()
        test_sec = math.ceil(self.test_until - time.time()) if self.test != "none" else 0

        def dist(c):   # -1 = nothing in range / no echo (the faults tell the two apart)
            return round(c.dist, 1) if 0 < c.dist < math.inf else -1
        return {
            "id": self.cfg["name"], "mac": self.mac, "fw": "sim-2.4.0", "ip": "simulator",
            "ssid": self.nets[0] if self.nets else "", "host": host, "rssi": random.randint(-62, -55),
            "up": int(t), "boot": "power on", "apName": "CamelGuard-Setup-" + self.mac.replace(":", "")[-4:],
            "ack": self.ack, "alert": self.alert, "test": self.test, "testSec": test_sec, "led": self.led,
            "state": self.state, "hazard": self.hazard,
            "hazardSec": int(t - self.hazard_start) if self.state in ("WARNING", "DANGER") else 0,
            "camel": self.camel_on, "camelDir": self.camel_dir, 
            "irA": self.gA.raw, "irB": self.gB.raw,
            "distA": dist(self.uA), "distB": dist(self.uB), "carA": self.uA.car, "carB": self.uB.car,
            "vehA": self.veh_a, "vehB": self.veh_b,
            "veh": self.veh_a or self.veh_b, "vehCount": self.veh_count, "vehCountA": self.uA.count,
            "vehCountB": self.uB.count, "lastDir": self.last_dir, "camelCount": self.camel_count,
            **self.power_view(),
            "sig": sig, "signC": sc, "signW": sw, "signA": s_a, "signB": s_b, "buzz": bz,
            "faults": self.faults(), "cfg": self.cfg, "nets": self.nets,
        }


class SimCrossing:
    """One simulated ESP32 with its own model highway."""

    def __init__(self, idx, name, scenario, args, label):
        self.label = label
        self.host = f"{args.host}:{args.port}"
        self.world = World(scenario, self.log)
        # the third crossing of the "multi" scenario has no INA219 (a USB-powered device)
        self.dev = Device(name, f"5A:17:00:00:01:{idx + 1:02X}", f"http://{args.host}:{args.port}/api/telemetry", self.log,
                          power=not (scenario == "clear" and name == "sim-crossing-c"))

    def log(self, msg):
        print(f">> {self.label}{msg}")

    def run_second(self):
        """20 steps of 50 ms per second: sensors sampled every step, evaluation every other step, like loop()."""
        dev = self.dev
        for k in range(20):
            w = self.world.step(0.05)
            dev.t += 0.05
            dev.sample_car(dev.uA, w["dA"])
            dev.sample_car(dev.uB, w["dB"])
            dev.sample_gate(w["irA"], w["irB"])
            dev.power_step(0.05)
            if k % 2:
                dev.evaluate()
        body = dev.telemetry(self.host)
        sent_ack = body["ack"]
        try:
            req = urllib.request.Request(dev.url, json.dumps(body).encode(), {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=2) as r:
                dev.last_sent_ack = sent_ack   # the server has now seen this ack
                dev.handle(json.loads(r.read()))
            if dev.restart_after_ack and dev.last_sent_ack == dev.restart_after_ack:
                dev.restart()
        except OSError as e:
            self.log(f"Server unreachable: {e}")
        return (f"{self.label}{dev.state:7s}{' ALERT' if dev.alert else ''}"
                f"{' TEST:' + dev.test if dev.test != 'none' else ''} {dev.hazard or '-':14s} "
                f"camel={int(dev.camel_on)}{' from ' + dev.camel_dir if dev.camel_on else ''} IR A={int(dev.gA.raw)} "
                f"B={int(dev.gB.raw)} vehicles={dev.veh_count}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=default_port(), help="dashboard port (default: camel_guard.ini)")
    ap.add_argument("--scenario", default="auto", choices=SCENARIOS)
    ap.add_argument("--devices", type=int, default=1, help="number of simulated crossings (1-10)")
    args = ap.parse_args()

    if args.scenario == "multi":
        plan = [("sim-crossing-a", "auto"), ("sim-crossing-b", "camel"), ("sim-crossing-c", "clear")]
    else:
        n = clamp(args.devices, 1, 10)
        plan = [("esp32-simulator" if n == 1 else f"sim-crossing-{chr(97 + i)}", args.scenario if i == 0 else "clear")
                for i in range(n)]
    multi = len(plan) > 1
    sims = [SimCrossing(i, name, scen, args, f"[{name}] " if multi else "") for i, (name, scen) in enumerate(plan)]
    print(f"Simulator sending to http://{args.host}:{args.port}/api/telemetry (Ctrl+C to stop)")
    for s, (name, scen) in zip(sims, plan):
        print(f"  {name:16s} {s.dev.mac}  scenario: {scen}")

    while True:
        t0 = time.time()
        for s in sims:
            print(s.run_second())
        time.sleep(max(0.05, 1 - (time.time() - t0)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
