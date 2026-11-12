"""Generates docs/wiring_diagram.svg (1640 x 1540, white canvas) for the Camel Guard project."""
import sys

W, H = 1640, 1540
out = []
F = "Segoe UI, system-ui, Arial, sans-serif"
MONO = "Consolas, monospace"


def e(s):
    out.append(s)


def text(x, y, s, size=12, anchor="start", weight="normal", fill="#111827", mono=False):
    fam = f' font-family="{MONO}"' if mono else ""
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    e(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" fill="{fill}"{fam}>{s}</text>')


def rect(x, y, w, h, fill="#f3f4f6", stroke="#6b7280", rx=8, sw=1.5):
    e(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')


def wire(points, color, width=2.5, dash=None):
    pts = " ".join(f"{x},{y}" for x, y in points)
    d = f' stroke-dasharray="{dash}"' if dash else ""
    e(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"{d}/>')


def dot(x, y, color):
    e(f'<circle cx="{x}" cy="{y}" r="4" fill="{color}"/>')


POWER = {"5V": "#dc2626", "3V3": "#ea580c", "GND": "#111827", "12V": "#7c3aed"}


def tag(x, y, label, anchor="start"):
    """Power tag at the end of a short stub: 5V / 3V3 / GND / 12V."""
    c = POWER[label]
    w = 9 + 6.5 * len(label)
    x0 = x if anchor == "start" else x - w
    e(f'<rect x="{x0}" y="{y - 8}" width="{w}" height="16" rx="3" fill="{c}"/>')
    text(x0 + w / 2, y + 4, label, 10, "middle", "bold", "#ffffff")


def power_stub(x, y, label, length=28, side="right"):
    c = POWER[label]
    if side == "right":
        wire([(x, y), (x + length, y)], c)
        tag(x + length, y, label)
    else:
        wire([(x, y), (x - length, y)], c)
        tag(x - length, y, label, "end")


def resistor_h(x, y, w=36, label="", color="#374151"):
    e(f'<rect x="{x}" y="{y - 6}" width="{w}" height="12" rx="2" fill="#fef3c7" stroke="{color}" stroke-width="1.5"/>')
    if label:
        text(x + w / 2, y - 10, label, 10, "middle", "bold", "#374151")


def resistor_v(x, y, h=30, label="", color="#374151"):
    e(f'<rect x="{x - 6}" y="{y}" width="12" height="{h}" rx="2" fill="#fef3c7" stroke="{color}" stroke-width="1.5"/>')
    if label:
        text(x + 10, y + h / 2 + 4, label, 10, "start", "bold", "#374151")


def pinlabel(x, y, s, anchor="end"):
    text(x, y + 4, s, 11, anchor, "bold", "#374151", True)


e(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="{F}">')
e(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
text(40, 45, "Camel Crossing Highway Safety (Camel Guard) — Wiring Diagram", 24, weight="bold")
text(40, 68, "Miniature simulator · firmware 2.4.0 · camel gate: 2× IR obstacle · cars: 2× HC-SR04 at the 400 m points · 1× 74HC595 (sides A + B in parallel) · "
     "buzzer · solar panel + battery measured by 2× INA219 (I2C)", 13, fill="#4b5563")

# ------------------------------------------------------------------ ESP32 board
BX0, BX1, BY0 = 620, 880, 270
rect(BX0, BY0, BX1 - BX0, 570, "#1f2937", "#1f2937", 10)
text(750, 258, "ESP32 DevKit V1 (30-pin)", 14, "middle", "bold")
rect(690, 380, 120, 150, "#374151", "#6b7280", 4)
text(750, 450, "ESP-WROOM-32", 11, "middle", "bold", "#e5e7eb")
text(750, 468, "(top view)", 10, "middle", fill="#9ca3af")
rect(728, 826, 44, 26, "#9ca3af", "#9ca3af", 3)
text(750, 870, "USB", 11, "middle", "bold", "#4b5563")
LEFT = ["EN", "VP", "VN", "D34", "D35", "D32", "D33", "D25", "D26", "D27", "D14", "D12", "D13", "GND", "VIN"]
RIGHT = ["D23", "D22", "TX0", "RX0", "D21", "D19", "D18", "D5", "D17", "D16", "D4", "D2", "D15", "GND", "3V3"]
USED = {"D32", "D33", "D25", "D26", "D27", "D14", "D13", "D17", "D16", "GND", "VIN",
        "D23", "D19", "D18", "D2", "3V3"}
PIN = {}
for i, n in enumerate(LEFT):
    y = 300 + 36 * i
    PIN["L" + n] = (BX0, y)
    e(f'<circle cx="{BX0}" cy="{y}" r="5" fill="#d1d5db" stroke="#9ca3af"/>')
    text(BX0 + 12, y + 4, n, 12, "start", "bold" if n in USED else "normal", "#ffffff" if n in USED else "#6b7280", True)
for i, n in enumerate(RIGHT):
    y = 300 + 36 * i
    PIN["R" + n] = (BX1, y)
    e(f'<circle cx="{BX1}" cy="{y}" r="5" fill="#d1d5db" stroke="#9ca3af"/>')
    text(BX1 - 12, y + 4, n, 12, "end", "bold" if n in USED else "normal", "#ffffff" if n in USED else "#6b7280", True)
# power pins
power_stub(BX0, 768, "GND", 26, "left")
power_stub(BX0, 804, "5V", 26, "left")
text(BX0 + 50, 808, "(5 V in)", 10, "start", fill="#9ca3af")
power_stub(BX1, 768, "GND", 26)
power_stub(BX1, 804, "3V3", 26)
text(BX1 + 12, 713, "(onboard LED)", 9, "start", fill="#9ca3af")


def route_left(y_from, lane, pin, color, x_from=300):
    px, py = PIN["L" + pin]
    wire([(x_from, y_from), (lane, y_from), (lane, py), (px, py)], color)


def route_right(y_from, lane, pin, color, x_from=1180):
    px, py = PIN["R" + pin]
    wire([(x_from, y_from), (lane, y_from), (lane, py), (px, py)], color)


# ------------------------------------------------------------------ left column
LX0, LX1 = 30, 300


def module(y0, h, title, sub, pins, color="#f3f4f6"):
    """Box on the left column with its pins on the right edge. pins: [(y, name)]"""
    rect(LX0, y0, LX1 - LX0, h, color)
    text(LX0 + 12, y0 + 20, title, 13.5, weight="bold")
    text(LX0 + 12, y0 + 36, sub, 10.5, fill="#4b5563")
    for y, n in pins:
        pinlabel(LX1 - 8, y, n)


# 1. power supply summary (the INA219 modules and the power path are drawn at the bottom)
rect(LX0, 88, LX1 - LX0, 76, "#fffbeb", "#d97706")
text(LX0 + 12, 108, "Power: solar panel + 18650 battery", 13.5, weight="bold")
text(LX0 + 12, 125, "TP4056 → 18650 → MT3608 → 5 V (VIN)", 10.5, fill="#4b5563")
text(LX0 + 12, 141, "2× INA219 on I2C: D17 = SDA · D16 = SCL", 10.5, fill="#4b5563")
text(LX0 + 12, 156, "see the power path at the bottom of the page", 10, fill="#b45309", weight="bold")


def us_module(y0, side, trig_pin, echo_pin, lane_t, lane_e, col_t, col_e):
    """HC-SR04 ultrasonic sensor: VCC 5 V, TRIG straight to the ESP32, ECHO (5 V!) through a 1k / 2k divider."""
    module(y0, 120, f"HC-SR04 — VEHICLE {side}", f"side-{side} · 400 m point · beside the lane, across",
           [(y0 + 22, "VCC"), (y0 + 42, "TRIG"), (y0 + 68, "ECHO"), (y0 + 94, "GND")])
    power_stub(LX1, y0 + 22, "5V", 22)
    power_stub(LX1, y0 + 94, "GND", 22)
    route_left(y0 + 42, lane_t, trig_pin, col_t)
    ye = y0 + 68
    wire([(LX1, ye), (318, ye)], col_e)
    resistor_h(318, ye, 40, "1 kΩ")
    wire([(358, ye), (410, ye)], col_e)
    dot(410, ye, col_e)
    wire([(410, ye), (410, ye + 8)], "#374151", 1.8)
    resistor_v(410, ye + 8, 26, "2 kΩ")
    wire([(410, ye + 34), (410, ye + 44)], "#111827")
    tag(396, ye + 52, "GND")
    route_left(ye, lane_e, echo_pin, col_e, x_from=410)


# 2. vehicle sensor A (D32 TRIG / D33 ECHO)
us_module(174, "A", "D32", "D33", 570, 550, "#65a30d", "#4d7c0f")

# 3. buzzer through an NPN transistor -> D25
B0 = 304
rect(LX0, B0, LX1 - LX0, 132, "#f3f4f6", "#6b7280")
text(LX0 + 12, B0 + 20, "Buzzer (active, 5 V)", 13.5, weight="bold")
text(LX0 + 12, B0 + 36, "at the crossing · via NPN 2N2222", 10.5, fill="#4b5563")
tag(92, B0 + 52, "5V")
wire([(110, B0 + 60), (110, B0 + 70)], "#dc2626")
e(f'<circle cx="110" cy="{B0 + 82}" r="12" fill="#111827"/>')
text(110, B0 + 86, "BZ", 9, "middle", "bold", "#ffffff")
wire([(110, B0 + 94), (110, B0 + 102), (148, B0 + 102)], "#374151", 2)
e(f'<circle cx="160" cy="{B0 + 110}" r="14" fill="#ffffff" stroke="#374151" stroke-width="1.8"/>')
wire([(152, B0 + 98), (152, B0 + 122)], "#374151", 2.5)
wire([(152, B0 + 104), (164, B0 + 98)], "#374151", 1.8)
wire([(152, B0 + 116), (166, B0 + 123)], "#374151", 1.8)
text(176, B0 + 128, "E→GND", 9, fill="#374151")
resistor_h(196, B0 + 110, 40, "1 kΩ")
wire([(152, B0 + 110), (196, B0 + 110)], "#374151", 1.8)
wire([(236, B0 + 110), (LX1, B0 + 110)], "#0f766e")
route_left(B0 + 110, 530, "D25", "#0f766e")

# 4. vehicle sensor B (D26 TRIG / D27 ECHO)
us_module(446, "B", "D26", "D27", 510, 490, "#16a34a", "#15803d")


def ir_module(y0, title, sub, pin, lane, color):
    """IR obstacle sensor (FC-51 type): VCC 3V3, GND, digital OUT (active LOW) to the ESP32."""
    module(y0, 76, title, sub, [(y0 + 22, "VCC"), (y0 + 40, "GND"), (y0 + 58, "OUT")])
    e(f'<circle cx="{LX0 + 200}" cy="{y0 + 58}" r="5" fill="#ef4444"/><circle cx="{LX0 + 214}" cy="{y0 + 58}" r="5" fill="#111827"/>')
    power_stub(LX1, y0 + 22, "3V3", 22)
    power_stub(LX1, y0 + 40, "GND", 22)
    route_left(y0 + 58, lane, pin, color)


# 5./6. camel gate IR sensors, one at each edge of the crossing
ir_module(576, "IR obstacle — CAMEL IR B", "camel gate · side-B edge of the crossing", "D14", 470, "#be185d")
ir_module(662, "IR obstacle — CAMEL IR A", "camel gate · side-A edge of the crossing", "D13", 450, "#db2777")
text(LX0 + 12, 758, "HC-SR04 (cars): beside each lane at the 400 m point, pinging across.", 10, fill="#b91c1c", weight="bold")
text(LX0 + 12, 772, "ECHO is 5 V: never connect it to the ESP32 without the divider.", 10, fill="#b91c1c", weight="bold")

# 74HC595 physical pinout (DIP-16, top view) for the breadboard
OY = 788
rect(LX0, OY, 500, 158, "#f9fafb", "#9ca3af", 8)
text(LX0 + 12, OY + 20, "74HC595 pinout — DIP-16, top view (notch on the left)", 12.5, weight="bold")
DX0, DP = LX0 + 70, 50
TOP = [(16, "VCC"), (15, "Q0"), (14, "DS"), (13, "OE"), (12, "ST_CP"), (11, "SH_CP"), (10, "MR"), (9, "Q7'")]
BOT = [(1, "Q1"), (2, "Q2"), (3, "Q3"), (4, "Q4"), (5, "Q5"), (6, "Q6"), (7, "Q7"), (8, "GND")]
e(f'<rect x="{DX0 - 22}" y="{OY + 60}" width="{7 * DP + 44}" height="50" rx="4" fill="#1f2937"/>')
e(f'<path d="M{DX0 - 22},{OY + 75} A10,10 0 0 1 {DX0 - 22},{OY + 95}" fill="#f9fafb"/>')
text(DX0 + 3.5 * DP, OY + 89, "74HC595", 12, "middle", "bold", "#e5e7eb")
e(f'<circle cx="{DX0 - 8}" cy="{OY + 100}" r="3" fill="#9ca3af"/>')
for i, ((nt, lt), (nb, lb)) in enumerate(zip(TOP, BOT)):
    x = DX0 + i * DP
    e(f'<rect x="{x - 5}" y="{OY + 50}" width="10" height="10" fill="#9ca3af"/><rect x="{x - 5}" y="{OY + 110}" width="10" height="10" fill="#9ca3af"/>')
    text(x, OY + 72, str(nt), 9.5, "middle", "bold", "#93c5fd", True)
    text(x, OY + 106, str(nb), 9.5, "middle", "bold", "#93c5fd", True)
    text(x, OY + 44, lt, 10, "middle", "bold", "#374151", True)
    text(x, OY + 134, lb, 10, "middle", "bold", "#374151", True)
text(LX0 + 12, OY + 151, "Pin 1 = dot / next to the notch. Blue numbers = pin numbers used in the diagram.", 9.5, fill="#6b7280")

# ------------------------------------------------------------------ right column: 1× 74HC595
# Schematic symbol with every pin numbered (DIP-16 pin number next to each pin):
# inputs / supply on the left, Q0 … Q7 in order and Q7' on the right.
CX0, CX1 = 1200, 1330          # chip body
STUB = 30                      # pin stub length


def pin_left(y, name, num):
    wire([(CX0 - STUB, y), (CX0, y)], "#374151", 2)
    text(CX0 - STUB / 2, y - 5, str(num), 10, "middle", "bold", "#2563eb", True)
    text(CX0 + 8, y + 4, name, 11, "start", "bold", "#ffffff", True)


def pin_right(y, name, num):
    wire([(CX1, y), (CX1 + STUB, y)], "#374151", 2)
    text(CX1 + STUB / 2, y - 5, str(num), 10, "middle", "bold", "#2563eb", True)
    text(CX1 - 8, y + 4, name, 11, "end", "bold", "#ffffff", True)


def hop_h(x1, x2, y, xs, color):
    """Horizontal wire from x1 to x2 that jumps (small arc) over vertical wires at xs."""
    d = f"M{x1},{y}"
    for x in sorted(xs):
        d += f" L{x - 6},{y} A6,6 0 0 1 {x + 6},{y}"
    d += f" L{x2},{y}"
    e(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linecap="round"/>')


def shift_register(y0, title, side):
    """The 74HC595 with its 5 LEDs (A + B in parallel). Returns the y of each left pin and of Q7'."""
    ys = {"DS": y0 + 30, "ST_CP": y0 + 62, "SH_CP": y0 + 94, "MR": y0 + 142, "OE": y0 + 174,
          "VCC": y0 + 206, "GND": y0 + 238}
    rect(CX0, y0, CX1 - CX0, 312, "#1f2937", "#1f2937", 6)
    text((CX0 + CX1) / 2, y0 - 12, title, 14, "middle", "bold")
    text((CX0 + CX1) / 2, y0 + 296, "74HC595", 10, "middle", "bold", "#9ca3af")
    for name, num in (("DS", 14), ("ST_CP", 12), ("SH_CP", 11), ("MR", 10), ("OE", 13), ("VCC", 16), ("GND", 8)):
        pin_left(ys[name], name, num)
    # fixed levels: MR high (no reset), OE low (outputs on), supply
    for name, lvl in (("MR", "3V3"), ("OE", "GND"), ("VCC", "3V3"), ("GND", "GND")):
        power_stub(CX0 - STUB, ys[name], lvl, 12, "left")
    text(CX0 + 8, y0 + 266, "100 nF: 16 – 8", 9, "start", fill="#9ca3af")
    # outputs Q0 (pin 15), Q1…Q7 (pins 1…7), Q7' (pin 9)
    rows = [("Yellow lamp (always flashing)", "#f59e0b"), ("Warning 300 m (A + B parallel)", "#fbbf24"),
            ("Warning 200 m (A + B parallel)", "#fbbf24"), ("Warning 100 m (A + B parallel)", "#fbbf24"),
            ("Camel crossing sign (A + B parallel)", "#fbbf24")]
    for q in range(8):
        y = y0 + 30 + q * 30
        pin_right(y, f"Q{q}", 15 if q == 0 else q)
        x = CX1 + STUB
        if q >= len(rows):
            text(x + 8, y + 4, "spare (not connected)", 10, fill="#6b7280")
            continue
        label, col = rows[q]
        resistor_h(x + 6, y, 26)
        wire([(x, y), (x + 6, y)], "#374151", 1.8)
        wire([(x + 32, y), (x + 42, y)], "#374151", 1.8)
        e(f'<circle cx="{x + 51}" cy="{y}" r="8" fill="{col}" stroke="#374151" stroke-width="1.5"/>')
        text(x + 66, y - 2, label, 10.5, weight="bold")
        text(x + 66, y + 10, "220 Ω → LED(s) → GND", 9, fill="#6b7280")
    y7 = y0 + 30 + 8 * 30 + 12
    pin_right(y7, "Q7'", 9)
    return ys, y7


ys1, q7a = shift_register(110, "74HC595 — SIDES A + B IN PARALLEL", "")
text(CX1 + STUB + 8, q7a + 4, "not connected", 10, fill="#6b7280")

# ESP32 → the 74HC595
route_right(ys1["DS"], 940, "D23", "#0284c7", CX0 - STUB)
route_right(ys1["ST_CP"], 965, "D18", "#7c3aed", CX0 - STUB)
PX, PY = PIN["RD19"]
wire([(PX, PY), (990, PY), (990, ys1["SH_CP"])], "#c2410c")
hop_h(990, CX0 - STUB, ys1["SH_CP"], [], "#c2410c")
text(CX0 - 150, 450, "Each output drives the side-A and the", 10.5, "start", "bold", "#374151")
text(CX0 - 150, 464, "side-B lamp / sign together (parallel).", 10.5, "start", "bold", "#374151")

# ------------------------------------------------------------------ power and notes
rect(560, 910, 380, 150, "#fff7ed", "#ea580c", 8)
text(575, 932, "Power", 14, weight="bold")
for i, line in enumerate([
    "• Supply: solar panel → TP4056 → 18650 → MT3608 (5.1 V) → VIN",
    "• A 5 V ≥ 2 A USB adapter / power bank works too (bench, backup)",
    "• 5 V rail (VIN): buzzer, 2× HC-SR04",
    "• 3V3 rail (ESP32 pin): 2× IR, 74HC595, 2× INA219",
    "• All GND connected together (ESP32, sensors, LEDs, power)",
]):
    text(575, 954 + i * 17, line, 11, fill="#374151")

rect(980, 838, 560, 340, "#f9fafb", "#9ca3af", 8)
text(995, 858, "Notes", 14, weight="bold")
notes = [
    "1. Camel gate: IR A (side-A edge) · IR B (side-B edge).",
    "   Camel = an IR blocked ≥ 3 readings; first IR = its side. No PIR.",
    "2. IR: FC-51, VCC 3.3 V, OUT active LOW.",
    "3. Cars: 2× HC-SR04 at the 400 m points, ≤ 15 cm across the lane = car.",
    "   ECHO is a 5 V signal: always through the 1 kΩ / 2 kΩ divider.",
    "4. Yellow lamp: always flashing; side A + B lamps on one output (Q0).",
    "5. D0, D2, D5, D12, D15 are strapping pins: no sensor on them.",
    "6. Free: D4 (old PIR), D21 / D22, D34, D35, VP, VN.",
    "7. Self-test at power-on: Q0 → Q4 in turn, then a short beep.",
    "8. A + B in parallel = 2 LEDs per output, each with its own 220 Ω (≈ 12 mA);",
    "    bigger signs: NPN or ULN2803 driver.",
    "9. 74HC595: VCC 16 & MR 10 → 3V3 · OE 13 & GND 8 → GND ·",
    "    100 nF across pins 16–8. Q0 = pin 15, Q1…Q4 = pins 1…4.",
    "10. INA219 #1 (0x40) on the solar input, #2 (0x41, A0 bridged) on the",
    "    battery: positive current = charging. Not fitted = USB still works.",
]
for i, line in enumerate(notes):
    text(995, 880 + i * 19, line, 11, fill="#374151")

rect(30, 956, 500, 200, "#f9fafb", "#9ca3af", 8)
text(45, 978, "Wire colours", 14, weight="bold")
legend = [("#dc2626", "5 V"), ("#ea580c", "3.3 V"), ("#111827", "GND"), ("#a21caf", "I2C SDA ← D17"), ("#ca8a04", "I2C SCL ← D16"),
          ("#65a30d", "Vehicle HC-SR04 A TRIG ← D32"), ("#4d7c0f", "Vehicle HC-SR04 A ECHO → D33"),
          ("#16a34a", "Vehicle HC-SR04 B TRIG ← D26"), ("#15803d", "Vehicle HC-SR04 B ECHO → D27"),
          ("#db2777", "Camel IR A → D13"), ("#be185d", "Camel IR B → D14"), ("#0f766e", "Buzzer ← D25"),
          ("#0284c7", "595 DS (14) ← D23"), ("#7c3aed", "595 ST_CP (12) ← D18"), ("#c2410c", "595 SH_CP (11) ← D19"),
]
for i, (c, l) in enumerate(legend):
    col, row = i % 2, i // 2
    x, y = 45 + col * 240, 1002 + row * 18
    wire([(x, y), (x + 26, y)], c, 4)
    text(x + 34, y + 4, l, 11, fill="#374151")

# ------------------------------------------------------------------ power path: solar panel + battery, 2× INA219
BY = 1196
BOX_Y, BOX_H = 1296, 118          # row of modules
ROW = BOX_Y + 80                  # main current path (panel -> INA #1 -> charger -> node -> INA #2 -> cell)
GY = BOX_Y + 104                  # ground pins
rect(30, BY, 1580, 316, "#fffbeb", "#d97706", 10)
text(48, BY + 24, "Solar panel + battery — power supply and measurement (2× INA219 on the I2C bus)", 15, weight="bold")
PV, BT = "#d97706", "#1d4ed8"      # wire colours: solar side / battery side


def vtag(x, y, label):
    """power tag centred on x, y (for stubs that run up from a module)"""
    c = POWER[label]
    e(f'<rect x="{x - 14}" y="{y - 8}" width="28" height="16" rx="3" fill="{c}"/>')
    text(x, y + 4, label, 10, "middle", "bold", "#ffffff")


def hop_v(x, y1, y2, ys, color, width=2.5):
    """vertical wire from y1 to y2 that jumps (small arc) over horizontal wires at ys"""
    d = f"M{x},{y1}"
    for y in sorted(ys):
        d += f" L{x},{y - 6} A6,6 0 0 1 {x},{y + 6}"
    d += f" L{x},{y2}"
    e(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round"/>')


def box(x0, y0, w, h, title, sub, fill="#f3f4f6", stroke="#6b7280"):
    rect(x0, y0, w, h, fill, stroke)
    text(x0 + 10, y0 + 20, title, 13, weight="bold")
    text(x0 + 10, y0 + 35, sub, 10, fill="#4b5563")


def ina(x0, title, sub):
    """INA219 breakout: supply + I2C on the top edge, VIN+ left / VIN- right with the 0.1 ohm shunt between them."""
    w = 200
    rect(x0, BOX_Y, w, BOX_H, "#f3f4f6", "#6b7280")
    for dx, lab in ((28, "VCC"), (60, "GND"), (110, "SDA"), (142, "SCL")):
        text(x0 + dx, BOX_Y + 17, lab, 9.5, "middle", fill="#374151", mono=True, weight="bold")
        e(f'<circle cx="{x0 + dx}" cy="{BOX_Y}" r="3.5" fill="#9ca3af"/>')
    text(x0 + 10, BOX_Y + 42, title, 13, weight="bold")
    text(x0 + 10, BOX_Y + 57, sub, 10, fill="#4b5563")
    resistor_h(x0 + 70, ROW, 60, "")
    text(x0 + 100, ROW + 22, "0.1 Ω shunt", 9.5, "middle", fill="#6b7280")
    wire([(x0, ROW), (x0 + 70, ROW)], "#374151", 2)
    wire([(x0 + 130, ROW), (x0 + w, ROW)], "#374151", 2)
    text(x0 + 8, ROW + 22, "VIN+", 10, fill="#374151", mono=True, weight="bold")
    text(x0 + w - 8, ROW + 22, "VIN−", 10, "end", fill="#374151", mono=True, weight="bold")
    wire([(x0 + 28, BOX_Y), (x0 + 28, BOX_Y - 12)], POWER["3V3"], 2)
    vtag(x0 + 28, BOX_Y - 20, "3V3")
    wire([(x0 + 60, BOX_Y), (x0 + 60, BOX_Y - 12)], "#111827", 2)
    vtag(x0 + 60, BOX_Y - 20, "GND")


# solar panel
box(50, BOX_Y, 120, BOX_H, "Solar panel", "6 V · 2 W")
for gx in range(3):
    for gy in range(2):
        rect(62 + gx * 32 - 2, BOX_Y + 46 + gy * 28, 28, 24, "#bfdbfe", "#1d4ed8", 2, 1)
text(162, ROW - 6, "+", 13, "end", "bold", PV)
text(162, GY - 6, "−", 13, "end", "bold", "#111827")
wire([(170, ROW), (250, ROW)], PV)
wire([(170, GY), (186, GY)], "#111827")
tag(186, GY, "GND")

# INA219 #1 on the panel
ina(250, "INA219 #1 — SOLAR", "0x40 · A0, A1 open")
wire([(450, ROW), (520, ROW)], PV)

# TP4056 charger
box(520, BOX_Y, 180, BOX_H, "TP4056 charger", "with battery protection")
text(528, ROW + 4, "IN+", 10, fill="#374151", mono=True, weight="bold")
text(692, ROW + 4, "B+", 10, "end", fill="#374151", mono=True, weight="bold")
text(528, GY + 4, "IN−", 10, fill="#374151", mono=True, weight="bold")
text(692, GY + 4, "B−", 10, "end", fill="#374151", mono=True, weight="bold")
wire([(520, GY), (506, GY)], "#111827")
tag(478, GY, "GND")
wire([(700, GY), (714, GY)], "#111827")
tag(714, GY, "GND")
wire([(700, ROW), (790, ROW)], BT)
dot(745, ROW, BT)

# INA219 #2 on the battery
ina(790, "INA219 #2 — BATTERY", "0x41 · A0 bridged to VS+")
wire([(990, ROW), (1050, ROW)], BT)
text(1020, ROW - 10, "+ = charging", 9.5, "middle", fill="#1d4ed8", weight="bold")

# battery cell
box(1050, BOX_Y, 130, BOX_H, "18650 Li-ion", "3.7 V · ≈ 2600 mAh")
e(f'<rect x="1068" y="{BOX_Y + 46}" width="64" height="30" rx="4" fill="#dbeafe" stroke="{BT}" stroke-width="1.5"/>'
  f'<rect x="1132" y="{BOX_Y + 55}" width="6" height="12" fill="{BT}"/>')
text(1100, BOX_Y + 66, "cell", 10, "middle", fill=BT, weight="bold")
text(1058, ROW - 6, "+", 13, "start", "bold", BT)
wire([(1050, GY), (1036, GY)], "#111827")
tag(1008, GY, "GND")
text(1058, GY - 6, "−", 13, "start", "bold", "#111827")

# MT3608 boost converter from the battery node
MY = BOX_Y + BOX_H + 22
box(790, MY, 200, 58, "MT3608 boost → 5.1 V", "IN+ from the battery node")
wire([(745, ROW), (745, MY + 29), (790, MY + 29)], BT)
wire([(990, MY + 29), (1030, MY + 29)], POWER["5V"])
tag(1030, MY + 29, "5V")
text(1066, MY + 33, "→ VIN (5 V rail)", 10.5, fill="#b91c1c", weight="bold")
wire([(790, MY + 48), (776, MY + 48)], "#111827")
tag(748, MY + 48, "GND")

# I2C bus from the ESP32 (D17 = SDA, D16 = SCL) to both INA219
SDA_Y, SCL_Y = BY + 42, BY + 58
SDA_C, SCL_C = "#a21caf", "#ca8a04"
px, py = PIN["RD17"]
wire([(px, py), (952, py), (952, SDA_Y), (360, SDA_Y)], SDA_C)
hop_v(900, SDA_Y, BOX_Y, [SCL_Y], SDA_C)
dot(900, SDA_Y, SDA_C)
wire([(360, SDA_Y), (360, BOX_Y)], SDA_C)
px, py = PIN["RD16"]
wire([(px, py), (968, py), (968, SCL_Y), (392, SCL_Y)], SCL_C)
wire([(932, SCL_Y), (932, BOX_Y)], SCL_C)
dot(932, SCL_Y, SCL_C)
wire([(392, SCL_Y), (392, BOX_Y)], SCL_C)
text(560, SDA_Y - 6, "SDA ← D17", 10, fill=SDA_C, mono=True, weight="bold")
text(560, SCL_Y + 15, "SCL ← D16", 10, fill=SCL_C, mono=True, weight="bold")

# what the dashboard shows
text(1230, BY + 60, "What the ESP32 reports", 13, weight="bold")
for i, line in enumerate([
    "• INA219 #1: panel V, mA, W; “sun” above 4.5 V",
    "• INA219 #2: battery V (≈ %), mA; + charging, − discharging",
    "• States: charging · full (≥ 4.10 V) · on battery · idle",
    "• Battery low below 3.45 V (clears above 3.60 V)",
    "• Not fitted at power-on: skipped (USB still works)",
    "• Stops answering later: fault INA-S / INA-B",
    "• Wrong sign? swap VIN+ / VIN− or INA_*_INVERT in config.h",
]):
    text(1230, BY + 84 + i * 20, line, 11, fill="#374151")
wire([(1230, BY + 252), (1256, BY + 252)], PV, 4)
text(1264, BY + 256, "solar side (panel → charger)", 11, fill="#374151")
wire([(1230, BY + 272), (1256, BY + 272)], BT, 4)
text(1264, BY + 276, "battery side (charger / boost node ↔ cell)", 11, fill="#374151")

e("</svg>")
open(sys.argv[1], "w", encoding="utf-8").write("\n".join(out))
print("wrote", sys.argv[1])
