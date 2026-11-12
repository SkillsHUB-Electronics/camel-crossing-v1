const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, ImageRun, AlignmentType, HeadingLevel,
  WidthType, ShadingType, BorderStyle, LevelFormat, PageBreak, Header, Footer, PageNumber, TableOfContents,
  VerticalAlign,
} = require("docx");

const ROOT = process.argv[2];
const IMG = path.join(ROOT, "proposal", "images");
const OUT = path.join(ROOT, "proposal", process.argv[3] || "Camel_Crossing_Highway_Safety_Proposal.docx");

const FONT = "Calibri";
const ACCENT = "B45309";   // desert amber
const DARK = "1F2937";
const CONTENT_W = 9638;    // A4 with 1" / 0.8" margins → 11906 - 2*1134

// ---------------------------------------------------------------- helpers
const t = (text, o = {}) => new TextRun({ text, font: FONT, size: o.size || 22, bold: o.bold, italics: o.italics, color: o.color });
const p = (text, o = {}) => new Paragraph({
  children: Array.isArray(text) ? text : [t(text, o)],
  spacing: { after: o.after ?? 120, line: 300 }, alignment: o.align || AlignmentType.JUSTIFIED,
});
const h1 = text => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun({ text, font: FONT })], spacing: { before: 240, after: 140 } });
const h2 = text => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text, font: FONT })], spacing: { before: 180, after: 100 } });
const bullet = (text, lvl = 0) => new Paragraph({
  numbering: { reference: "bullets", level: lvl }, spacing: { after: 60, line: 290 },
  children: Array.isArray(text) ? text : [t(text)],
});
const num = (text, ref = "numbers") => new Paragraph({ numbering: { reference: ref, level: 0 }, spacing: { after: 60, line: 290 }, children: [t(text)] });
const bold = (b, rest) => [t(b, { bold: true }), t(rest)];

function img(file, widthPx, heightPx, caption) {
  const maxW = 600;
  const w = Math.min(maxW, widthPx), h = Math.round(heightPx * w / widthPx);
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 60 },
      children: [new ImageRun({ type: "png", data: fs.readFileSync(path.join(IMG, file)), transformation: { width: w, height: h },
        altText: { title: caption, description: caption, name: file } })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
      children: [t(caption, { italics: true, size: 19, color: "4B5563" })] }),
  ];
}

const border = { style: BorderStyle.SINGLE, size: 4, color: "C7CDD6" };
const borders = { top: border, bottom: border, left: border, right: border };
function cell(text, width, o = {}) {
  const runs = (Array.isArray(text) ? text : [text]).map(x => typeof x === "string" ? t(x, { size: o.size || 19, bold: o.bold, color: o.color }) : x);
  return new TableCell({
    borders, width: { size: width, type: WidthType.DXA }, verticalAlign: VerticalAlign.CENTER,
    shading: o.fill ? { fill: o.fill, type: ShadingType.CLEAR, color: "auto" } : undefined,
    margins: { top: 60, bottom: 60, left: 90, right: 90 },
    children: [new Paragraph({ alignment: o.align || AlignmentType.LEFT, children: runs })],
  });
}
function table(headers, rows, widths, o = {}) {
  const total = widths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: total, type: WidthType.DXA }, columnWidths: widths,
    rows: [
      new TableRow({ tableHeader: true, children: headers.map((hd, i) => cell(hd, widths[i], { bold: true, fill: DARK, color: "FFFFFF" })) }),
      ...rows.map((r, ri) => new TableRow({
        children: r.map((c, i) => cell(c, widths[i], {
          fill: (o.fills && o.fills(ri, i)) || (ri % 2 ? "F5F7FA" : undefined),
          bold: o.boldRow && o.boldRow(ri), align: o.align && o.align(i),
        })),
      })),
    ],
  });
}
const spacer = () => new Paragraph({ spacing: { after: 120 }, children: [] });

// ---------------------------------------------------------------- data
const budget = [
  // [item, spec, qty, unit AED, category]
  ["ESP32 DevKit V1 (30-pin)", "Dual-core 240 MHz, WiFi, 12-bit ADC", 1, 35, "Electronics"],
  ["Ultrasonic sensor HC-SR04", "Car sensors at the 400 m point of each approach, across the lane, 5 V", 2, 12, "Electronics"],
  ["IR obstacle sensor FC-51 (digital OUT)", "Camel gate: one at each edge of the crossing (side A / side B)", 2, 6, "Electronics"],
  ["74HC595 shift register", "one chip: yellow lamp, 3 warning signs, camel sign (+3 spare); sides A and B in parallel", 1, 3, "Electronics"],
  ["LED kit 5 mm (yellow/amber)", "Yellow caution lamps and warning-sign LEDs", 1, 12, "Electronics"],
  ["Resistor kit (220 Ω, 1 k, 2 k, 100 k)", "LED limiting, ECHO dividers, buzzer transistor", 1, 20, "Electronics"],
  ["Active buzzer 5 V + NPN 2N2222", "Warning at the crossing (herder)", 1, 10, "Electronics"],
  ["Yellow LEDs 5 mm (extra)", "Two lamps per output (side A + B) and the flashers on the 300/200/100 m warning signs", 1, 8, "Electronics"],
  ["INA219 current / voltage sensor module (I2C)", "One on the solar panel, one on the battery", 2, 12, "Electronics"],
  ["Solar panel 6 V 2 W", "Charges the battery in daylight", 1, 30, "Power"],
  ["TP4056 charger module with battery protection", "1 A Li-ion charger, over-discharge protection", 1, 6, "Power"],
  ["18650 Li-ion cell + holder", "≈ 2600 mAh, 3.7 V", 1, 25, "Power"],
  ["MT3608 boost converter", "Battery 3.7 V → 5.1 V for the ESP32, HC-SR04 and buzzer", 1, 5, "Power"],
  ["5 V 2 A USB adapter + cable", "Bench supply and backup", 1, 25, "Power"],
  ["Breadboard 830 points", "Prototyping", 2, 15, "Prototyping"],
  ["Jumper wire set (M-M, M-F)", "Connections", 1, 20, "Prototyping"],
  ["Perfboard + pin headers", "Final soldered circuit", 1, 15, "Prototyping"],
  ["MDF base board 150 × 60 cm", "Miniature highway base", 1, 55, "Miniature"],
  ["Toy camels (≈ 1:24)", "Camel figures, 10–12 cm tall", 3, 10, "Miniature"],
  ["Warning sign boards ×6 (300/200/100 m)", "Printed faces on sticks, two flasher LEDs each", 1, 25, "Miniature"],
  ["Toy cars (≈ 1:24–1:32)", "Vehicles for the vehicle sensors", 3, 8, "Miniature"],
  ["Paint, sand texture, road tape, median, fence", "Desert highway scenery with camel fence", 1, 40, "Miniature"],
  ["Sensor gates, posts & sign boards (3D print / acrylic)", "Car-sensor posts at the 400 m points (HC-SR04), camel gate posts (IR), sign faces", 1, 40, "Miniature"],
  ["Consumables (glue, heat-shrink, screws, tape)", "Assembly", 1, 25, "Misc."],
];
const subtotal = budget.reduce((a, r) => a + r[2] * r[3], 0);
const contingency = Math.round(subtotal * 0.10);

const weeks = 12;
const plan = [
  // [task, from, to, phase color]
  ["Research, proposal & approval", 1, 1, "BFDBFE"],
  ["System design: block diagram, schematic, BOM", 1, 2, "BFDBFE"],
  ["Order & receive components", 2, 3, "E5E7EB"],
  ["Sensor tests on breadboard (HC-SR04, IR obstacle)", 3, 4, "FDE68A"],
  ["Output tests (74HC595: yellow lamp, signs, buzzer)", 4, 4, "FDE68A"],
  ["Solar panel + battery build, INA219 tests", 5, 6, "FDE68A"],
  ["Firmware: detection + state machine", 4, 6, "FDE68A"],
  ["Firmware: WiFi, telemetry, settings (NVS)", 5, 6, "FDE68A"],
  ["Dashboard + simulator integration", 6, 7, "DDD6FE"],
  ["Miniature road model build", 6, 8, "FED7AA"],
  ["Install electronics in the model, solder board", 8, 9, "FED7AA"],
  ["Sensor adjustment & unit / integration tests", 9, 10, "BBF7D0"],
  ["System tests, fixes, data collection", 10, 11, "BBF7D0"],
  ["Report, poster, demo video", 11, 12, "E9D5FF"],
  ["Final presentation & exhibition", 12, 12, "FECACA"],
];

const tests = [
  ["T-01", "Unit", "Car sensor at the 400 m point", "Empty lane, then hold a block at 5, 10, 15 cm across the lane from each sensor, 10 readings each (dashboard: Sensors → Sensors Now)", "Empty lane reads 'Lane Empty'; distance error ≤ ±1 cm on both sensors"],
  ["T-02", "Unit", "Camel gate sensors", "Pass a hand / figure through IR A and IR B 10 times each (adjust the pots)", "IR blocked only while something is in the gate; nothing shown when the gate is empty"],
  ["T-03", "Unit", "Camel detection and direction", "Walk a camel figure through the gate from side A to B 20 times, then from B to A 20 times", "≥ 18/20 confirmed in each direction (≥ 90 %); correct side shown; counted once per crossing"],
  ["T-04", "Unit", "Warning signs 300/200/100 m", "Place a camel on the crossing 10 times; watch all six signs", "All six flash (wig-wag) within 0.5 s, both directions, 10/10"],
  ["T-05", "Unit", "Short IR flicker rejection", "Break IR A and IR B for less than 0.15 s (a quick hand wave), 10 times each", "No warning: an IR must stay blocked for the confirmation readings (3 × 50 ms)"],
  ["T-06", "Unit", "Vehicle counting (HC-SR04)", "Roll a car past the 400 m sensor of side A 20 times, then side B 20 times (no camel)", "20/20 counted on the correct side, no double counts; signs stay off without a camel"],
  ["T-07", "Unit", "Sensor fault detection", "Unplug the ECHO wire of vehicle sensor A for 5 s; later keep IR A blocked for more than 2 minutes", "Fault US-A within 2 s and OBJ-A after 2 min on the dashboard; both clear when fixed"],
  ["T-09", "Unit", "Camel from side B", "Walk the camel into the gate from side B only, 10 times", "WARNING every time; dashboard shows 'From Side B'"],
  ["T-10", "Unit", "Outputs", "Settings → Output Test: buzzer, yellow lamp, signs, each LED one by one", "Each output works; each LED lights in the order named by the dashboard; test stops after the set time"],
  ["T-11", "Integration", "Detection latency", "Place a camel figure; timestamp video of sign LED", "Sign flashing ≤ 0.5 s after placement"],
  ["T-12", "Integration", "DANGER escalation per side", "Camel at the gate, then roll a car past the 400 m sensor of side A, later side B", "Signs of that car's side flash fast + buzzer ≤ 0.3 s after the sensor fires; the other side keeps the normal flash"],
  ["T-13", "Integration", "Yellow lamp", "Film the yellow lamp with and without a camel at the gate", "Flashes at 1 Hz in every state, never off; side A and B lamps flash together"],
  ["T-14", "Integration", "Hold & clear", "Remove the figure and time the return to CLEAR", "Clear after hold time ±0.5 s; red ≥ 2 s then green; no flicker"],
  ["T-15", "Integration", "Approach window", "Camel at the gate; one car passes a 400 m sensor; time the DANGER state", "DANGER for the approach time (default 8 s) ±0.3 s, then back to WARNING"],
  ["T-16", "System", "Works without WiFi", "Switch the WiFi off, repeat T-03 and T-12", "Local warnings still work; dashboard shows OFFLINE"],
  ["T-17", "System", "Dashboard telemetry", "Compare dashboard with serial monitor for 10 min", "≤ 2 s delay; no missing hazard in the log"],
  ["T-18", "System", "Settings from the dashboard", "Change IR confirmation, vehicle distance, hold time and approach time", "Applied on the ESP32 and confirmed ('Applied')"],
  ["T-19", "System", "Manual alert (all crossings)", "Overview → ALERT ALL, then STOP ALERT", "All online crossings warn within 2 s and stop"],
  ["T-20", "System", "Endurance", "Run the model for 2 hours with random scenarios", "No reset, no false DANGER, log complete"],
  ["T-22", "System", "Power measurement (INA219)", "Compare the dashboard with a multimeter: battery voltage and current, panel voltage and current", "Voltage within ±0.05 V and current within ±10 % of the multimeter"],
  ["T-23", "System", "Charge-state detection", "Panel under a lamp; then cover it; then leave the full battery in the light", "Charging (+ current) in light; On battery (− current) when covered; Full at ≥ 4.10 V; changes logged"],
  ["T-24", "System", "Low-battery warning", "Lower the battery voltage below 3.45 V (variable supply or discharge), then raise it above 3.60 V", "“Battery low” on the dashboard and in the log below 3.45 V; clears above 3.60 V"],
  ["T-25", "System", "Battery runtime and INA fault", "Run on the battery only until the low-battery warning; unplug the SDA wire of INA219 #2", "Runtime ≥ 4 h; fault INA-B within 2 s, cleared when reconnected; no INA fitted = no fault"],
  ["T-21", "Acceptance", "Full demonstration", "Scripted demo: clear → camel from side A → car from side A → camel from side B → car from side B → hand through IR only → clear", "All states shown correctly on model and dashboard"],
];

const risks = [
  ["A hand, a toy car or a reflection breaks a camel IR beam", "Medium", "An IR must stay blocked for the confirmation readings (adjustable); adjust the module range pot; a camel IR blocked for 2 minutes raises fault OBJ (test T-05)"],
  ["The car sensor across the lane sees the far roadside or fence", "Medium", "Median of three readings; detection limit (lane width) adjustable with the live reading shown; fault CAR after 2 min"],
  ["The two ultrasonic sensors hear each other", "Medium", "Sensors ping in turns, 25 ms apart (firmware)"],
  ["HC-SR04 5 V ECHO damages the ESP32 input", "High", "1 kΩ / 2 kΩ voltage divider on both ECHO lines, checked with a multimeter before connecting"],
  ["IR camel sensors disturbed by sunlight or shiny / dark surfaces", "Medium", "Indoor demo; matt surfaces; small shield tube on each module; adjust the pot"],
  ["Li-ion cell damaged by over-discharge, over-charge or a short circuit", "High", "TP4056 module with protection circuit; insulated wiring; battery-low warning at 3.45 V; never leave it charging unattended"],
  ["Too little light to charge the battery indoors", "Medium", "Use a lamp for the demo; USB adapter as backup; charge state shown on the dashboard"],
  ["INA219 reads the current with the wrong sign", "Low", "Swap VIN+ / VIN− or set the invert flag in config.h; checked by test T-23"],
  ["Component delivery delay", "Medium", "Order in week 2; simulator allows firmware/dashboard work meanwhile"],
  ["School WiFi (Enterprise) cannot be used by the ESP32", "High", "Phone hotspot / router; ESP32 works fully offline"],
];

// ---------------------------------------------------------------- document
const children = [];

// cover
children.push(
  new Paragraph({ spacing: { before: 600, after: 120 }, alignment: AlignmentType.CENTER, children: [t("Electrical Engineering (ENE) Graduation Project", { size: 28, bold: true, color: "4B5563" })] }),
  new Paragraph({ spacing: { after: 60 }, alignment: AlignmentType.CENTER, children: [t("G11 · Term 3 · Capstone Project Proposal", { size: 24, color: "6B7280" })] }),
  new Paragraph({ spacing: { before: 500, after: 200 }, alignment: AlignmentType.CENTER, children: [t("Advanced Camel Crossing Hazard Detection & Highway Safety System", { size: 48, bold: true, color: ACCENT })] }),
  new Paragraph({ spacing: { after: 400 }, alignment: AlignmentType.CENTER, children: [t("“Camel Guard” — an ESP32 smart-highway miniature simulator with live IoT dashboard", { size: 26, italics: true, color: DARK })] }),
  ...img("block_diagram.png", 1400, 720, "Figure 0 — System overview"),
);
children.push(table(["Project Information", ""], [
  ["School", "ATS-DHF"],
  ["Supervisor", "Haitham Zein"],
  ["Students", "1. Mohamed Mubarak Almansoori   2. Mohammed Jumaa   3. Mansoor Abdulla   4. Ibrahim Jumaa"],
  ["Project type", "Miniature simulator (table-top road model) — not a field installation"],
  ["Document", "Full proposal: design, wiring, firmware, dashboard, budget, timeline and testing plan"],
], [2400, 7238], { fills: (r, i) => i === 0 ? "FEF3C7" : undefined, boldRow: () => false }));
children.push(new Paragraph({ children: [new PageBreak()] }));

// contents
children.push(new Paragraph({ children: [t("Contents", { size: 32, bold: true, color: DARK })], spacing: { after: 160 } }));
children.push(new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }));
children.push(p("(If the list above is empty, right-click it in Word and choose “Update Field”.)", { size: 18, color: "6B7280", italics: true }));
children.push(new Paragraph({ children: [new PageBreak()] }));

// 1
children.push(h1("1. Overview"));
children.push(p("The Engineering Capstone Project allows students to apply engineering knowledge and practical skills to solve real-world problems. This project focuses on improving road safety on UAE highways through the development of a smart warning system for camel crossings."));
children.push(p("Because the project is a school capstone, the system is built and demonstrated as a miniature simulator: a table-top desert highway model with toy camels and toy cars. There are no pedestrians on a highway, so the system concentrates entirely on camels. The electronics, firmware and dashboard are real and complete; only the scale of the road is reduced. Everything in this proposal — sensor positions, timings and tests — is therefore specified for the model. The warning signs at 300, 200 and 100 m are placed in the correct order but not to scale."));

// 2
children.push(h1("2. Project Title and Description"));
children.push(p([t("Title: ", { bold: true }), t("Advanced Camel Crossing Hazard Detection & Highway Safety System (“Camel Guard”).")]));
children.push(p("The system watches a camel crossing on a divided highway and warns drivers automatically. A camel gate at the crossing has two infrared (IR) obstacle sensors — one at the side-A edge and one at the side-B edge. An ESP32 microcontroller treats an IR sensor that stays blocked for a few readings as a camel; the IR sensor that fired first tells from which side the camel is coming. There is no PIR sensor. While a camel is crossing, advance warning signs 300 m, 200 m and 100 m before the crossing flash in both directions, and the camel-crossing sign flashes. A yellow caution lamp at the camel gate flashes all the time, whether a camel is present or not; the lamps and signs of side A and side B are wired in parallel, so one 74HC595 drives both sides. Two ultrasonic (HC-SR04) sensors stand beside the road at the “400 m” point of each approach, before the 300 m sign, and detect a car passing that point; when a car passes while a camel is at the crossing, the system escalates to DANGER: the roadside warning signs flash fast and a buzzer at the crossing warns the camel herder."));
children.push(p("All decisions are made on the ESP32 itself, so the warnings keep working without WiFi. In addition, the ESP32 sends its data every second to a PC dashboard that shows a live animated view of the road, charts, a hazard history, a vehicle log and a settings page from which every setting can be changed without re-programming the board. The model is powered by a solar panel and a battery; two INA219 sensors measure the panel and the battery, so the dashboard also shows the battery charge, the charging condition and a low-battery warning."));

// 3
children.push(h1("3. Problem Statement"));
children.push(p("On UAE highways camels may cross unexpectedly, especially at night and in low visibility. Collisions with camels are particularly severe because of the animal’s height and weight: at highway speed the body strikes the windscreen of a car. Drivers need time to slow down, but traditional warning signs are static — they are always shown, so drivers become used to them, and they give no information about whether a camel is actually on the road right now. There is a need for a smart, automated system that detects a camel at the crossing, warns approaching drivers early enough (hundreds of metres before the crossing) only when it matters, stops the traffic, and records what happened so the road authority can see how often and when camels cross."));

// 4
children.push(h1("4. Abstract"));
children.push(p("The Camel Guard system is an embedded IoT prototype designed to improve safety at camel crossings on UAE highways. It is demonstrated on a 150 × 60 cm miniature highway model. A camel gate with two IR obstacle sensors (one at each edge of the crossing) recognises camels: an IR blocked for a few readings is a camel, the first IR gives the direction, and a hold timer keeps the warning on while the animal crosses. Two HC-SR04 ultrasonic sensors at the 400 m point of each approach, beside the lane and pinging across it, detect passing cars (median of three readings); a car passing during a camel makes the signs flash fast. An ESP32 runs a four-state machine (WARMUP, CLEAR, WARNING, DANGER) and drives, through one 74HC595 shift register (side A and side B in parallel), the advance warning signs at 300 / 200 / 100 m that flash while a camel is crossing, the camel-crossing sign and an always-flashing yellow caution lamp — plus a buzzer at the crossing. The device is powered by a solar panel and an 18650 battery; two INA219 sensors on the I2C bus measure the panel and the battery (voltage, current, charging condition)."));
children.push(p("The ESP32 sends JSON telemetry over WiFi to a local Python dashboard that provides a live road view, charts, event and hazard logs, a vehicle log per direction, remote configuration stored in the ESP32’s flash, output tests, multi-crossing overview and firmware tools. A software simulator reproduces the firmware logic so the dashboard can be developed and demonstrated without hardware. The expected outcome is a reliable, low-cost and energy-efficient smart-road demonstrator that shows how sensing, embedded decision making and IoT monitoring can reduce accident risk and support the UAE’s smart-city initiatives."));

// 5
children.push(h1("5. Background Research"));
children.push(p("Camel-related road accidents are a known problem on desert highways in the UAE and the wider Gulf region. Current countermeasures are fences along highways, designated camel crossings with static signs, and awareness campaigns. Fences guide animals to crossings, which makes the crossing points the right place for active detection. At highway speed a driver needs several hundred metres to slow down safely, which is why this system warns with flashing signs at 300, 200 and 100 m before the crossing."));
children.push(h2("5.1 Detection technologies considered"));
children.push(table(["Technology", "Strengths", "Limitations", "Use in this project"], [
  ["Ultrasonic (HC-SR04)", "Detects an object in its beam and its distance; works day and night; cheap", "Range ≤ 4 m; soft surfaces echo weakly; ECHO is a 5 V signal", "Car sensors at the 400 m point of each approach (across the lane)"],
  ["IR obstacle sensor (FC-51)", "Cheap, fast, simple digital output, adjustable range, 3.3 V", "Short range (2–30 cm); reacts to any object; affected by strong sunlight", "Camel gate: one at each edge (gives the direction)"],
  ["INA219 (I2C)", "Measures voltage and current (0.1 mA steps with a 0.1 Ω shunt); two addresses share one bus", "Shunt must be in series with the supply; up to 3.2 A", "Solar panel and battery monitoring"],
  ["Radar / LiDAR", "Long range, all weather", "Costly, complex", "Future work (real road)"],
  ["AI camera", "Can recognise animals", "Needs computing power, privacy, night lighting", "Out of scope (future work)"],
], [1700, 2300, 2900, 2738]));
children.push(spacer());
children.push(p("Microcontroller: the ESP32 was chosen instead of the Arduino Uno used in the first draft because it has built-in WiFi (needed for the dashboard), two processor cores (sensing on one core, networking on the other), more memory, a 12-bit ADC and flash storage for settings — at a similar cost."));

// 6
children.push(h1("6. Aims and Objectives"));
children.push(p("Aim: to design, build and test a miniature smart highway-safety system that detects camels at a camel crossing and warns drivers automatically from 300 m before the crossing, with remote monitoring."));
[
  "Detect a camel figure crossing in either direction in at least 90 % of trials, report the side it came from, using only the two IR sensors of the camel gate.",
  "Flash all six advance warning signs (300 / 200 / 100 m, both directions) within 0.5 s of a camel entering the crossing.",
  "Show a yellow caution lamp that flashes continuously at the camel gate, and flash the warning signs 300 / 200 / 100 m while a camel is crossing.",
  "Detect a car passing the 400 m point and, if a camel is at the gate, escalate to DANGER within 0.3 s (that side’s signs flash fast + buzzer).",
  "Count every vehicle passing a vehicle sensor on the correct side (at least 95 % of trials) and log the vehicles that approached during a hazard.",
  "Measure the solar panel and the battery (voltage, current, charging condition) and warn on the dashboard when the battery is low.",
  "Keep all warnings working when WiFi or the PC is unavailable (edge decision on the ESP32).",
  "Provide a dashboard with live view, logs, history and remote settings, updated every second.",
  "Complete the project within 12 weeks and within the budget in Section 11.",
].forEach(x => children.push(num(x)));

// 7
children.push(h1("7. Project Scope"));
children.push(h2("7.1 Included"));
[
  "A working miniature highway model (about 1:24 scale) with one camel crossing and its camel gate, two car-sensor posts at the 400 m points, a yellow caution lamp, the camel-crossing sign and the advance warning signs (sides A and B in parallel).",
  "Electronics: ESP32, a camel gate with 2 IR obstacle sensors, 2 HC-SR04 ultrasonic sensors (vehicle), one 74HC595 (5 LED outputs, side A and B in parallel) and a buzzer.",
  "Power: solar panel, TP4056 charger with protection, 18650 battery, MT3608 boost converter and two INA219 monitors (I2C).",
  "Firmware with detection, state machine, WiFi telemetry, settings in flash, setup access point and USB configuration.",
  "PC dashboard (Python, runs on the school laptop) and a simulator for testing without hardware.",
  "Wiring diagram, testing plan with results, report, poster and demonstration.",
].forEach(x => children.push(bullet(x)));
children.push(h2("7.2 Not included"));
[
  "Installation on a real road, certification or approval by road authorities.",
  "AI camera recognition, radar or long-range sensors (listed as future improvements).",
  "Mobile phone notifications to drivers (future improvement).",
  "Vehicle speed measurement: one vehicle sensor per side detects presence and direction only (future improvement).",
  "Pedestrians: a highway has no pedestrian crossings, so pedestrian detection is not part of this system.",
].forEach(x => children.push(bullet(x)));

// 8
children.push(h1("8. System Design"));
children.push(h2("8.1 Block diagram"));
children.push(...img("block_diagram.png", 1400, 720, "Figure 1 — System block diagram"));
children.push(h2("8.2 How the camel crossing is monitored"));
children.push(p("The camel fence guides the animals into a camel gate at the crossing. IR sensor A watches the side-A edge of the crossing and IR sensor B the side-B edge. The ESP32 reads both every 50 ms:"));
children.push(p([t("camel = IR A or IR B blocked for the confirmation readings", { bold: true })], { align: AlignmentType.CENTER }));
children.push(table(["What the sensors see", "Result", "Confirmation", "Shown on the dashboard"], [
  ["IR A blocked", "CAMEL from side A", "IR blocked 3 readings in a row (0.15 s)", "WARNING · From Side A"],
  ["IR B blocked", "CAMEL from side B", "same", "WARNING · From Side B"],
], [2700, 1800, 2700, 2438]));
children.push(spacer());
children.push(p("The IR sensor that fired first gives the direction; when the camel leaves the gate through the other IR sensor it is the same camel and is counted once. The warning is held while an IR sensor still sees the animal and for a hold time (default 5 s) after that, so the signs do not flicker while it moves. The dashboard shows a sensor fault if a camel IR stays blocked for two minutes (OBJ)."));
children.push(h2("8.3 State machine and outputs"));
children.push(...img("state_machine.png", 1400, 600, "Figure 2 — Road state machine"));
children.push(table(["State", "Condition", "Yellow lamp", "Warning signs", "Buzzer"], [
  ["WARMUP", "first 3 s after power-on", "flashing", "off", "off"],
  ["CLEAR", "no camel at the gate", "flashing", "off", "off"],
  ["WARNING", "camel confirmed (IR) or manual alert", "flashing", "300/200/100 m signs + camel sign flash (1 Hz)", "off"],
  ["DANGER", "WARNING + a car passed a 400 m sensor within the approach time", "flashing", "signs flash fast (4 Hz)", "fast beeps"],
], [1300, 3100, 1700, 2238, 1300], { fills: (r) => ["FEF3C7", "DCFCE7", "FFEDD5", "FEE2E2"][r] }));
children.push(spacer());
children.push(h2("8.4 Advance warning signs (300 / 200 / 100 m)"));
children.push(p("Drivers on a highway need time to react, so the first warning appears long before the crossing. Three warning signs stand before the crossing on each approach, 300 m, 200 m and 100 m away. Each sign shows a camel and the distance, and has two amber flasher lamps on top. While a camel is crossing, the signs flash (1 Hz); when a vehicle is approaching the camel (DANGER) they flash fast (4 Hz). In the model the signs keep the correct order and spacing ratio but not the real distance. The signs of side A and side B are wired in parallel on the same output, so one 74HC595 drives both sides and they always show the same."));
children.push(table(["Output (74HC595)", "Sign", "Direction", "Behaviour"], [
  ["Q0 (pin 15)", "Yellow caution lamp", "A + B in parallel", "always flashing (1 Hz), in every state"],
  ["Q1 / Q2 / Q3 (pins 1, 2, 3)", "Warning signs 300 m / 200 m / 100 m", "A + B in parallel", "off → flashing while a camel is crossing → fast in DANGER"],
  ["Q4 (pin 4)", "Camel crossing sign", "A + B in parallel", "flashing in WARNING, fast in DANGER"],
  ["Q5 / Q6 / Q7 (pins 5, 6, 7)", "spare", "–", "not connected"],
], [1800, 2300, 1900, 3638]));
children.push(spacer());
children.push(h2("8.5 Yellow caution lamp"));
children.push(p("A yellow caution lamp at the camel gate flashes at 1 Hz all the time, in every state and whether a camel is present or not. The lamps of side A and side B are wired in parallel on one 74HC595 output (Q0), so both flash together. The red / amber / green signal sequence of earlier versions was removed to keep the circuit simple: the warning for the drivers is the yellow lamp plus the advance signs, and a vehicle that arrives is also warned by the fast-flashing signs and the buzzer."));
children.push(h2("8.6 Vehicle detection"));
children.push(p("One HC-SR04 ultrasonic sensor stands beside each approach at the “400 m” point, before the 300 m warning sign (about 400 m before the crossing on a real road; scaled on the model), and pings across its own lane. It is a pass-by detector: it does not measure how far a car is from the crossing, it only tells that a car is passing that point. An empty lane gives no echo in range (“Lane Empty”); a car in front of the sensor gives a short reading. Each sensor is read every 50 ms (the two take turns, 25 ms apart, so they cannot hear each other), the echo is timed by an interrupt and a median of three readings removes single wrong echoes. A reading at or below the detection limit (default 15 cm, adjustable 3–150 cm, set a little below the lane width) is a car. Every car that passes is counted for its side; a debounce of 0.3 s prevents one car from being counted twice. If a sensor gives no echo pulse at all for one second the dashboard shows fault US; if it reports a car for two minutes, fault CAR. After a car has passed the 400 m point, it is treated as approaching the crossing for the approach time (default 8 s, adjustable 1–20 s) — about the time a toy car needs from the sensor to the crossing. The roadside lamps (the 300 / 200 / 100 m warning signs and the camel-crossing sign) light only while a camel is at the gate; if a car passes during that time, the state becomes DANGER: the signs on that car’s side flash fast (4 Hz) and the buzzer sounds, while the signs of the other direction keep their normal 1 Hz flash. A car with no camel at the gate is only counted. With one sensor per side the system knows that a vehicle is coming and from which side, but it does not measure speed; a second sensor per side (speed trap) is listed as a future improvement."));

children.push(h2("8.7 Power supply and battery monitoring"));
children.push(p("The miniature highway runs from a small solar panel and a Li-ion battery; a USB adapter serves as bench supply and backup. The panel charges the 18650 cell through a TP4056 module that has a protection circuit, and an MT3608 boost converter turns the battery voltage into the 5.1 V that the ESP32, the HC-SR04 sensors and the buzzer need. Two INA219 modules measure the system over the I2C bus (two wires, SDA and SCL, shared by both): INA219 #1 sits between the panel and the charger and gives the panel voltage and current; INA219 #2 sits between the battery node and the cell and gives the battery voltage and the current into (+) or out of (−) the cell."));
children.push(table(["State shown", "Condition measured", "Meaning"], [
  ["Charging", "battery current > +20 mA", "the panel charges the battery"],
  ["Full", "battery ≥ 4.10 V and almost no current", "the TP4056 has finished charging"],
  ["On battery", "battery current < −20 mA", "no sun: the battery supplies the system"],
  ["Idle", "none of the above", "for example a USB supply or a balanced load"],
  ["Battery low", "battery below 3.45 V (until 3.60 V)", "warning on the dashboard and in the log"],
  ["No power tiles", "no INA219 answers at power-on", "USB-powered device: not fitted, no fault"],
], [1800, 3600, 4238], { fills: (r) => ["DCFCE7", "DCFCE7", "FEF3C7", "F5F7FA", "FEE2E2", "F5F7FA"][r] }));
children.push(spacer());
children.push(p("The battery charge is estimated from the cell voltage (3.30 V ≈ 0 %, 4.20 V = 100 %); under load the voltage sags, so the value is approximate. The ESP32 reads both sensors every 250 ms and smooths the values. A module that is not fitted is simply skipped, so the same firmware also runs on a USB-powered device; a module that answered once and then stops raises a sensor fault (INA-S or INA-B)."));
children.push(p("Power budget (an estimate, to be confirmed in test T-25): the electronics draw about 1 W (≈ 200 mA at 5 V). A 2600 mAh cell holds about 9.6 Wh, which gives roughly 8 hours without sun after the boost-converter losses. A 6 V / 2 W panel in full light delivers up to about 330 mA at 6 V, enough to run the system and charge the cell."));

// 9
children.push(h1("9. Hardware Design"));
children.push(h2("9.1 Components"));
children.push(table(["Component", "Function", "Key specification"], [
  ["ESP32 DevKit V1", "Main controller, WiFi", "240 MHz dual-core, 520 KB RAM, 3.3 V logic"],
  ["IR obstacle sensor ×2 (camel gate)", "Something at the side-A / side-B edge of the crossing", "FC-51, digital OUT active LOW, 3.3 V"],
  ["HC-SR04 ultrasonic ×2 (vehicle)", "Car passing the 400 m point of side A / B (beside the lane, across it)", "2–400 cm, 5 V, ECHO needs a 1 kΩ / 2 kΩ divider"],
  ["74HC595 ×1", "yellow lamp, 300/200/100 m signs, camel sign (side A + B in parallel)", "Still only 3 GPIO, 3.3 V supply"],
  ["LEDs + 220 Ω", "Yellow lamp, camel sign, warning-sign flashers (two LEDs per output)", "Yellow / amber 5 mm"],
  ["Active buzzer + NPN", "Warns the camel herder at the crossing", "5 V, driven via 2N2222 + 1 kΩ"],
  ["INA219 ×2 (I2C)", "#1 solar panel, #2 battery: voltage and current", "26 V / 3.2 A, 0.1 Ω shunt, addresses 0x40 and 0x41, 3.3 V"],
  ["Solar panel + TP4056 + 18650", "Daylight charging with battery protection", "6 V 2 W panel, 1 A charger, ≈ 2600 mAh cell"],
  ["MT3608 boost converter", "Battery 3.7 V → 5.1 V supply", "up to 2 A, set to 5.1 V"],
], [2600, 3600, 3438]));
children.push(h2("9.2 Pin assignment"));
children.push(table(["ESP32 pin", "Connected to", "ESP32 pin", "Connected to"], [
  ["GPIO 13", "Camel IR A (side-A edge) — OUT", "GPIO 23", "74HC595 DS (pin 14)"],
  ["GPIO 14", "Camel IR B (side-B edge) — OUT", "GPIO 18", "74HC595 SH_CP, pin 11"],
  ["GPIO 4", "free (was PIR)", "GPIO 19", "74HC595 ST_CP, pin 12"],
  ["GPIO 32", "Car HC-SR04 A (400 m) — TRIG", "GPIO 25", "Buzzer (via NPN)"],
  ["GPIO 33", "Car HC-SR04 A (400 m) — ECHO (divider)", "GPIO 2", "Onboard status LED"],
  ["GPIO 26", "Car HC-SR04 B (400 m) — TRIG", "GPIO 21 / 22", "free"],
  ["GPIO 27", "Car HC-SR04 B (400 m) — ECHO (divider)", "GPIO 17 / 16", "I2C SDA / SCL → INA219 #1 and #2"],
  ["–", "–", "VIN / 3V3", "5 V: HC-SR04, buzzer · 3.3 V: IR, 74HC595, INA219"],
], [1500, 3319, 1500, 3319]));
children.push(h2("9.3 Wiring diagram"));
children.push(...img("wiring_diagram.png", 1640, 1540, "Figure 3 — Wiring diagram (docs/wiring_diagram.svg)"));
children.push(h2("9.4 Miniature highway model"));
[
  "Base: 150 × 60 cm MDF board, desert sand texture, divided highway: carriageway A and carriageway B with a median, about 34 cm wide in total.",
  "One camel crossing in the middle with a camel fence along the road that guides the camels to it; the camel gate has an IR sensor post at each edge of the crossing.",
  "On each approach: warning signs 300 m, 200 m and 100 m before the crossing (evenly spaced, not to scale), then the camel-crossing sign; a yellow caution lamp stands at the crossing.",
  "One HC-SR04 car sensor beside each approach at the 400 m point, before the 300 m sign, pinging across the lane; a small ramp gives repeatable car runs for testing.",
  "Electronics box under the board; sensor wires run under the base. A small solar panel stands at the edge of the model, the battery and the power modules sit in the box.",
].forEach(x => children.push(bullet(x)));

// 10
children.push(h1("10. Software Design"));
children.push(h2("10.1 ESP32 firmware"));
[
  ["Edge decision: ", "sensing, classification and the state machine run on core 1 every 100 ms, independent of the network."],
  ["Telemetry: ", "a network task on core 0 sends JSON to the dashboard every second; the reply may carry one command."],
  ["Settings in flash (NVS): ", "IR confirmation readings, vehicle distance, hold time, approach time, sensors fitted (IR A, IR B), device name and up to five WiFi networks survive a power cycle."],
  ["Commands: ", "clear, change settings, change WiFi, manual alert, output test, reboot, factory reset."],
  ["Network recovery: ", "finds the dashboard PC automatically by UDP broadcast; starts its own setup WiFi (CamelGuard-Setup-XXXX) if no network works; can also be configured over the USB cable."],
  ["Power monitoring: ", "reads the two INA219 every 250 ms and derives the battery charge, the charging condition and a low-battery warning; works without them (USB-powered)."],
  ["Safety of settings: ", "every value is clamped to a safe range on both the PC and the ESP32, so a typing mistake cannot make the system blind."],
].forEach(([b, r]) => children.push(bullet(bold(b, r))));
children.push(h2("10.2 PC dashboard"));
children.push(...img("dashboard_live.png", 1440, 1720, "Figure 4 — Dashboard: live highway view and battery / solar monitoring (simulator)"));
[
  ["Overview: ", "every camel crossing as a card, hazards first; CLEAR ALL and ALERT ALL; today’s camel crossings and vehicles."],
  ["Dashboard: ", "animated live highway view (yellow lamps, 300/200/100 m warning signs, camel sign, the camel gate with IR A / IR B and the side the camel came from, the HC-SR04 car sensors at the 400 m points, cars from each side, buzzer), six live tiles, four battery / solar tiles, eight 10-minute charts (four of them for the power), “why the system decided this” and recent vehicles per side."],
  ["Log & History: ", "filtered event log, hazard history (duration, peak level, vehicles) and vehicle log, each exportable to CSV."],
  ["Settings: ", "detection (IR confirmation, approach time, hold time), live sensor view (IR, distances), detection limit (lane width), gate sensors fitted, WiFi, device, output test (including each LED one by one), wiring diagram, firmware compile/upload and ports."],
  ["Team: ", "authors and supervisor with photos."],
].forEach(([b, r]) => children.push(bullet(bold(b, r))));
children.push(h2("10.3 Simulator"));
children.push(p("A Python simulator reproduces the firmware exactly (same IR confirmation, direction, hold time, ultrasonic median filter, detection limit, approach window, yellow lamp and signs and command handling) and plays scenarios: auto tour, clear road, camel (from side A, later from side B), danger (camel plus cars from both sides) and three crossings at once. It also plays a 4-minute day / night cycle for the solar panel and the battery. It allows the dashboard to be developed and demonstrated before the hardware arrives."));

// 11 budget
children.push(h1("11. Materials and Budget"));
children.push(p("Prices are estimates in UAE dirham (AED) from local electronics and hobby suppliers and online stores; final prices depend on the supplier."));
const brows = budget.map(r => [r[0], r[1], String(r[2]), r[3].toFixed(0), (r[2] * r[3]).toFixed(0)]);
brows.push(["Subtotal", "", "", "", subtotal.toFixed(0)]);
brows.push(["Contingency (10 %)", "spares, shipping, price changes", "", "", contingency.toFixed(0)]);
brows.push(["TOTAL (required)", "", "", "", (subtotal + contingency).toFixed(0)]);
children.push(table(["Item", "Specification / purpose", "Qty", "Unit (AED)", "Total (AED)"], brows, [3100, 3400, 700, 1150, 1288], {
  boldRow: r => r >= budget.length, fills: r => r >= budget.length ? "FEF3C7" : undefined,
  align: i => i >= 2 ? AlignmentType.RIGHT : AlignmentType.LEFT,
}));
children.push(spacer());
children.push(p("Tools already available in the school workshop (not budgeted): laptop, Arduino IDE 2, soldering station, multimeter, hot-glue gun, 3D printer / laser cutter.", { size: 20, color: "4B5563" }));

// 12 timeline
children.push(h1("12. Project Timeline"));
children.push(p("Duration: 12 weeks (Term 3). Shaded cells show when each task is carried out."));
const tw = [3538], wk = 508;
for (let i = 0; i < weeks; i++) tw.push(wk);
const trows = plan.map(([task, a, b]) => [task, ...Array.from({ length: weeks }, () => "")]);
children.push(new Table({
  width: { size: tw.reduce((x, y) => x + y, 0), type: WidthType.DXA }, columnWidths: tw,
  rows: [
    new TableRow({ tableHeader: true, children: [cell("Task", tw[0], { bold: true, fill: DARK, color: "FFFFFF" }),
      ...Array.from({ length: weeks }, (_, i) => cell("W" + (i + 1), wk, { bold: true, fill: DARK, color: "FFFFFF", align: AlignmentType.CENTER, size: 17 }))] }),
    ...plan.map(([task, a, b, col]) => new TableRow({ children: [cell(task, tw[0], { size: 18 }),
      ...Array.from({ length: weeks }, (_, i) => cell("", wk, { fill: i + 1 >= a && i + 1 <= b ? col : undefined }))] })),
  ],
}));
children.push(spacer());
children.push(h2("12.1 Milestones"));
children.push(table(["Week", "Milestone / deliverable"], [
  ["1", "Proposal approved by the supervisor"],
  ["3", "All components received; bill of materials checked"],
  ["4", "All sensors and outputs working on the breadboard"],
  ["6", "Firmware with detection, state machine and dashboard connection"],
  ["8", "Miniature road model built"],
  ["10", "Integrated system adjusted; unit and integration tests passed"],
  ["11", "System tests passed; report and poster drafts"],
  ["12", "Final presentation and exhibition demonstration"],
], [1200, 8438]));

// 13 testing
children.push(h1("13. Testing Plan"));
children.push(p("Testing goes from single parts to the whole system: unit tests (each sensor and output), integration tests (detection with outputs) and system tests (dashboard, network, endurance), ending with an acceptance demonstration. Each test is repeated the stated number of times and recorded in the test log; a test passes only if the pass criterion is met."));
children.push(table(["ID", "Level", "Test", "Method", "Pass criterion"], tests, [700, 1150, 2000, 3150, 2638]));
children.push(spacer());
children.push(h2("13.1 Test equipment"));
["Ruler, stopwatch and a phone with slow-motion video (240 fps) for timing checks.",
  "Serial Monitor (115200 baud) and the dashboard event log for time stamps.",
  "A block and a ruler (5, 10, 15, 20 cm) for the ultrasonic tests, a multimeter (voltage and current) for the INA219 tests, a lamp for the solar panel, a ramp for repeatable toy-car runs, a small screwdriver for the IR potentiometers and masking tape.",
].forEach(x => children.push(bullet(x)));
children.push(h2("13.2 Test record (template)"));
children.push(table(["Test ID", "Date", "Trials", "Passed", "Result / measured value", "Pass?", "Tester"],
  [["T-03", "", "20", "", "", "", ""], ["T-06", "", "40", "", "", "", ""], ["", "", "", "", "", "", ""]],
  [900, 1100, 900, 900, 3200, 900, 1738]));

// 14 safety
children.push(h1("14. Safety Considerations"));
[
  "Low voltage only: 5 V from a USB adapter, or from the battery through a boost converter; no mains or 12 V parts.",
  "The 18650 cell is only used with a charger module that has a protection circuit; it is never short-circuited, punctured or left charging unattended.",
  "The IR sensors and the shift register run from 3.3 V; the 5 V ECHO signal of each HC-SR04 goes through a 1 kΩ / 2 kΩ divider, so every ESP32 input stays at its own 3.3 V logic level.",
  "All wires insulated, no exposed connections; the final circuit is soldered on perfboard inside an enclosure under the model.",
  "Hot-glue gun, soldering iron and cutting tools used only with teacher supervision and eye protection.",
  "The system is a demonstrator: it must not be installed on a real road without professional engineering and authority approval.",
].forEach(x => children.push(bullet(x)));
children.push(h2("14.1 Risk assessment"));
children.push(table(["Risk", "Likelihood", "Mitigation"], risks, [3700, 1300, 4638]));

// 15-17
children.push(h1("15. Advanced Innovative Features"));
[
  ["Advance warning at 300 / 200 / 100 m: ", "six signs flash in both directions while a camel is crossing, so drivers slow down early."],
  ["Two-sensor camel detection with direction: ", "an IR blocked for a few readings is a camel; the first IR shows from which side the camel comes."],
  ["Simple, parallel outputs: ", "one always-flashing yellow lamp and one 74HC595 serve both sides of the road."],
  ["Vehicle-aware escalation per side: ", "a car detected 400 m before the crossing makes the signs on its own side flash fast and sounds the buzzer — only when a camel is really at the gate."],
  ["Self-checking sensors: ", "sensor faults (no ultrasonic echo, vehicle sensor always occupied, IR blocked for minutes) are shown on the dashboard."],
  ["Edge decision + IoT dashboard: ", "works without WiFi; live road view, history and remote settings when connected."],
  ["Several crossings: ", "one dashboard monitors many crossings and can alert all of them at once."],
  ["Solar powered with live monitoring: ", "two INA219 sensors show the battery charge, the charging condition and the solar power on the dashboard and warn when the battery is low."],
].forEach(([b, r]) => children.push(bullet(bold(b, r))));

children.push(h1("16. Expected Outcomes"));
[
  "A working miniature smart highway that detects camels at the crossing and warns drivers automatically from 300 m before it.",
  "Measured results for detection accuracy, detection latency and vehicle counting (Section 13).",
  "A dashboard showing live status, history and vehicle statistics that a road authority could use.",
  "Documentation: wiring diagram, source code, test results, report and poster.",
].forEach(x => children.push(bullet(x)));

children.push(h1("17. Limitations"));
[
  "IR (2–30 cm) and HC-SR04 (≤ 4 m) ranges suit the model; a real highway needs radar, LiDAR or thermal cameras.",
  "The IR sensors detect an object at the gate but cannot tell a camel from another large animal or a person.",
  "With one pass-by sensor per side at the 400 m point the system knows that a car is coming and from which side, but not its speed or exact position.",
  "A 2 W panel indoors or in shade charges slowly, and the battery charge is estimated from its voltage, so the runtime is approximate.",
  "Soft, angled or very absorbent surfaces can give a weak echo (the sensor then reports “no echo”); IR sensors are affected by strong sunlight and heat, so the model is demonstrated under room lighting.",
].forEach(x => children.push(bullet(x)));

children.push(h1("18. Future Improvements"));
[
  "AI camera recognition of camels (edge AI) combined with the IR gate.",
  "A second ultrasonic sensor on each approach to measure vehicle speed and log speeding during a hazard.",
  "Mobile warnings to nearby drivers and navigation apps; connection to smart road / V2X systems.",
  "Radar or thermal sensing for real road distances; a larger solar panel and battery for a real roadside unit.",
  "Variable message sign showing distance to the hazard and the advised speed.",
].forEach(x => children.push(bullet(x)));

children.push(h1("19. Team Roles"));
children.push(table(["Student", "Main responsibility", "Shared tasks"], [
  ["Mohamed Mubarak Almansoori", "Project leader, hardware design and wiring", "Testing, report"],
  ["Mohammed Jumaa", "Firmware (ESP32) and sensor adjustment", "Testing, presentation"],
  ["Mansoor Abdulla", "Miniature road model and mechanical parts", "Poster, video"],
  ["Ibrahim Jumaa", "Dashboard, simulator and data analysis", "Testing, report"],
], [3000, 3900, 2738]));
children.push(p("Roles are a proposal and can be changed by the team with the supervisor.", { size: 19, italics: true, color: "6B7280" }));

children.push(h1("20. References"));
[
  "Espressif Systems — ESP32 Series Datasheet and ESP32 Technical Reference Manual.",
  "HC-SR04 Ultrasonic Ranging Module — user guide / datasheet.",
  "FC-51 IR obstacle avoidance sensor module (LM393 comparator) — datasheet.",
  "INA219 Zero-Drift, Bidirectional Current/Power Monitor With I2C Interface — datasheet (Texas Instruments).",
  "74HC595 8-bit serial-in / parallel-out shift register — datasheet (Texas Instruments / Nexperia).",
  "Arduino-ESP32 core documentation (WiFi, Preferences, HTTPClient, WebServer).",
  "ATS Electrical Engineering Capstone Project Workbook.",
].forEach(x => children.push(num(x, "refs")));
children.push(spacer());
children.push(p("Prepared according to the ATS Electrical Engineering Capstone Project Workbook requirements.", { italics: true, color: "4B5563", align: AlignmentType.CENTER }));

// ---------------------------------------------------------------- build
const doc = new Document({
  creator: "Camel Guard team",
  title: "Advanced Camel Crossing Hazard Detection & Highway Safety System — Proposal",
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, color: ACCENT, font: FONT }, paragraph: { spacing: { before: 280, after: 140 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, color: DARK, font: FONT }, paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 1 } },
    ],
  },
  numbering: { config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } },
                                     { level: 1, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1080, hanging: 270 } } } }] },
    { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 360 } } } }] },
    { reference: "refs", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "[%1]", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 360 } } } }] },
  ] },
  features: { updateFields: true },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1134, bottom: 1134, left: 1134, right: 1134 } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT,
      border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: ACCENT, space: 4 } },
      children: [t("Camel Guard — Camel Crossing Highway Safety System · ENE Capstone Proposal", { size: 17, color: "6B7280" })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [new TextRun({ children: ["Page ", PageNumber.CURRENT, " of ", PageNumber.TOTAL_PAGES], font: FONT, size: 18, color: "6B7280" })] })] }) },
    children,
  }],
});
Packer.toBuffer(doc).then(buf => { fs.writeFileSync(OUT, buf); console.log("wrote", OUT, buf.length, "bytes; budget", subtotal, contingency, subtotal + contingency); });
