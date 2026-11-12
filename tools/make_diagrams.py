"""Generates docs/block_diagram.svg and docs/state_machine.svg (Camel Guard 2.4.0: IR camel gate, HC-SR04 vehicle sensors).

  python tools/make_diagrams.py
"""
import os

DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1400 {h}" width="1400" height="{h}" '
        'font-family="Segoe UI, Arial, sans-serif"><defs><marker id="a" markerWidth="10" markerHeight="10" refX="8" '
        'refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#475569"/></marker></defs>'
        '<rect width="1400" height="{h}" fill="#fff"/>')


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def box(x, y, w, h, title, lines, fill, stroke):
    s = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
    s += f'<text x="{x + w / 2}" y="{y + 26}" font-size="16" font-weight="700" text-anchor="middle" fill="#111827">{esc(title)}</text>'
    for i, l in enumerate(lines):
        s += f'<text x="{x + w / 2}" y="{y + 48 + i * 19}" font-size="13" text-anchor="middle" fill="#374151">{esc(l)}</text>'
    return s


def arrow(x1, y1, x2, y2, label="", c="#475569"):
    s = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{c}" stroke-width="2.5" marker-end="url(#a)"/>'
    if label:
        s += f'<text x="{(x1 + x2) / 2}" y="{(y1 + y2) / 2 - 7}" font-size="12" text-anchor="middle" fill="#475569">{esc(label)}</text>'
    return s


def block():
    h = 720
    s = HEAD.format(h=h)
    s += '<text x="40" y="44" font-size="24" font-weight="700" fill="#111827">Camel Guard — System Block Diagram (highway camel crossing)</text>'
    s += ('<text x="40" y="68" font-size="14" fill="#4b5563">Camel gate (2 IR obstacle) + 2 HC-SR04 (vehicle) sensors + 2 INA219 '
          '(solar panel, battery) → ESP32 edge decision → yellow lamp, warning signs, buzzer; WiFi telemetry → PC dashboard</text>')
    ins = [("Camel IR A (D13)", "camel gate · side-A edge"), ("Camel IR B (D14)", "camel gate · side-B edge"),
           ("HC-SR04 car sensor A", "side A · 400 m point · across the lane"), ("HC-SR04 car sensor B", "side B · 400 m point · across the lane"),
           ("2× INA219 (I2C)", "solar panel V / A · battery V / A")]
    for i, (t, l) in enumerate(ins):
        y = 92 + i * 100
        s += box(40, y, 300, 72, t, [l], "#fffbeb" if i == 4 else "#fdf2f8" if i < 2 else "#eff6ff",
                 "#d97706" if i == 4 else "#db2777" if i < 2 else "#2563eb")
        s += arrow(340, y + 36, 470, 365, c="#d97706" if i == 4 else "#475569")
    s += box(470, 240, 340, 250, "ESP32 DevKit V1", ["camel = an IR blocked for several readings", "side = the IR that fired first · hold timer", "car passing 400 m point: HC-SR04 ≤ carCm",
                                                    "car + camel → signs flash fast + buzzer", "yellow lamp always flashing", "state machine:",
                                                    "CLEAR · WARNING · DANGER"], "#fef3c7", "#d97706")
    outs = [("74HC595 (A + B in parallel)", "yellow lamp · 300/200/100 m · camel sign"),
            ("Buzzer (NPN)", "at the crossing")]
    for i, (t, l) in enumerate(outs):
        y = 160 + i * 130
        s += box(960, y, 300, 76, t, [l], "#ecfdf5", "#16a34a")
        s += arrow(810, 365, 960, y + 38)
    s += box(960, 540, 300, 110, "PC Dashboard (Python)", ["live highway view · charts · logs", "settings · output test · firmware"],
             "#f5f3ff", "#7c3aed")
    s += '<line x1="640" y1="480" x2="640" y2="595" stroke="#7c3aed" stroke-width="2.5" stroke-dasharray="8 6"/>'
    s += arrow(640, 595, 960, 595, "WiFi · JSON telemetry every 1 s", "#7c3aed")
    s += '<text x="660" y="540" font-size="12" fill="#7c3aed">commands back (settings, CLEAR, alert, test)</text>'
    s += box(465, 570, 165, 90, "Power", ["solar → TP4056 → 18650", "→ 5 V boost (or USB)"], "#fff7ed", "#ea580c")
    return s + "</svg>"


def states():
    h = 600
    s = HEAD.format(h=h)
    s += '<text x="40" y="44" font-size="24" font-weight="700" fill="#111827">Road State Machine (runs on the ESP32 every 100 ms)</text>'
    st = [(60, "WARMUP", "first 3 s after power-on", "", "#fef3c7", "#d97706", "yellow lamp flashing"),
          (400, "CLEAR", "no camel at the gate", "", "#dcfce7", "#16a34a", "lamp flashing, signs off"),
          (740, "WARNING", "camel confirmed (IR)", "(or manual alert)", "#ffedd5", "#ea580c", "300/200/100 m signs flash"),
          (1080, "DANGER", "WARNING + car passed the", "400 m point (A or B)", "#fee2e2", "#dc2626", "signs flash + buzzer")]
    for x, n, a, b, f, c, o in st:
        s += f'<rect x="{x}" y="110" width="260" height="140" rx="16" fill="{f}" stroke="{c}" stroke-width="3"/>'
        s += f'<text x="{x + 130}" y="146" font-size="20" font-weight="800" text-anchor="middle" fill="{c}">{n}</text>'
        s += f'<text x="{x + 130}" y="176" font-size="13" text-anchor="middle" fill="#374151">{esc(a)}</text>'
        s += f'<text x="{x + 130}" y="195" font-size="13" text-anchor="middle" fill="#374151">{esc(b)}</text>'
        s += f'<text x="{x + 130}" y="230" font-size="12" font-weight="600" text-anchor="middle" fill="#111827">{esc(o)}</text>'
    for i in range(3):
        x = st[i][0] + 260
        s += arrow(x, 180, x + 80, 180)
    s += ('<text x="40" y="292" font-size="15" font-weight="700" fill="#111827">Yellow caution lamp: always flashing (1 Hz), in every state — camel or not.</text>')
    s += '<text x="40" y="324" font-size="16" font-weight="700" fill="#111827">Decision rules</text>'
    rules = ["IR trigger = camel IR A or B blocked for `confirm` readings of 50 ms in a row (default 3)",
             "camel = a camel IR confirmed (blocked as above); the first IR gives the side; no PIR",
             "a camel is held while an IR still sees it, then holdSec (5 s); leaving through the other IR counts once",
             "car approaching = HC-SR04 at the 400 m point of side A / B read ≤ carCm across the lane within approachSec (8 s)",
             "hazard = camel OR manual alert; hazard + vehicle approaching = DANGER",
             "the decision never depends on WiFi or the PC; the dashboard only monitors, logs and configures"]
    for i, r in enumerate(rules):
        s += f'<text x="60" y="{356 + i * 30}" font-size="14" fill="#374151">• {esc(r).replace("`", "")}</text>'
    return s + "</svg>"


if __name__ == "__main__":
    for name, fn in (("block_diagram.svg", block), ("state_machine.svg", states)):
        with open(os.path.join(DOCS, name), "w", encoding="utf-8") as fh:
            fh.write(fn())
        print("wrote", name)
