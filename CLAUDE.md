# CLAUDE.md — Camel Guard (camel crossing highway safety, ESP32) — handoff notes

## Working with the user
- The user writes in **Indonesian** — reply in Indonesian.
- Dashboard, firmware, comments, Serial messages and the proposal are **English**. `README.md` is English,
  `README.id.md` the Indonesian copy — keep both in sync. `docs/UJI_HARDWARE.id.md` is Indonesian only.
- Windows, Arduino IDE 2, Python 3.12, no git repo. Folder lives in OneDrive. **Several source files use CRLF line
  ends** (`config.h`, `.ino`, `server.py`, …) — when patching with scripts match on LF and keep the file's own line ends.
- The project is a **miniature simulator** (table-top highway model with toy camels / cars), never a real road device.
- **It is a highway — no pedestrians** (user, 2026-09-28). Advance warning signs at 300 / 200 / 100 m (both
  directions). The folder is still called `Camel Pedestrian`; proposal title "Advanced Camel Crossing Hazard
  Detection & Highway Safety System" (my wording — the user may want to confirm it with the school).
- "plan.md" does not exist in this project: when the user says "plan" they mean the **proposal** (`proposal/make_proposal.js`).
- Built on 2026-09-23 as a sibling of `..\Alarm System` (fire alarm) and deliberately reuses its architecture:
  settings in NVS, one-command-per-POST channel with ack, deferred reboot/factory, UDP discovery, setup AP, USB
  Web-Serial config, multi-device keyed by MAC, output test, firmware tools, Team page. Read `..\Alarm System\CLAUDE.md`
  for the reasoning behind those parts; the gotchas listed there apply here too.

## Hardware history (the user changes the sensors often — read the newest line)
1. v1.x: 2× HC-SR04 + PIR + LDR + 2× IR speed trap + relay beacon.
2. 2026-09-28: IR-only (4× IR) — firmware 2.0.x.
3. 2026-10-05 (firmware 2.1.0): camel sensors = 2× HC-SR04 again, vehicle sensors = 2× IR (D13 / D14).
4. 2026-10-05 later (firmware 2.2.0): + solar panel / battery measured by 2× INA219. The old D35 battery divider is gone.
5. **2026-10-18 (current, firmware 2.3.0): sensors swapped.** Vehicle sensors = **2× HC-SR04** (same pins D32/D33, D26/D27
   and ECHO dividers, moved to the approaches), camel sensors = **camel gate: 2× IR obstacle** (same D13 / D14, at the side-A /
   side-B edge of the crossing) **+ 1× PIR on D4** in the middle. User asked for it (Indonesian) via the project coordinator.
6. **2026-10-18 later (firmware 2.3.1): HC-SR04 placement corrected by the user** — they are NOT for the car-to-gate distance:
   each stands beside its lane at the "400 m" point (before the 300 m sign) and pings across it = pass-by detector. A car there
   triggers the roadside lamps of ITS side. User chose (option 1): lamps light only with a camel at the gate; a car at a side's
   400 m point → that side's 300/200/100 m signs + camel sign flash fast (4 Hz) + buzzer for approachSec, other side 1 Hz.
   No camel → car only counted, lamps stay off.
7. **2026-11-12 (current, firmware 2.4.0): simplification requested by the user (Indonesian).** **PIR removed** (D4 free): a camel
   = an IR trigger alone (blocked `confirm` readings), direction = first IR. **No red/amber/green signals**: one **yellow lamp that
   always flashes (1 Hz)**, camel or not, in every state (WARMUP too); side A and B are wired **in parallel on one output**. The
   300/200/100 m warning signs (and the camel sign) are paralleled between the sides too → **one 74HC595** (not two). HC-SR04 car
   sensors and the local/cloud dashboard modes are unchanged. Consequences: the 300/200/100 m signs + camel sign **flash 1 Hz** while a camel crosses (2.4.1, user request 2026-11-12: no car
   dependence; they do NOT go fast any more); a car at EITHER 400 m sensor only raises DANGER + buzzer. Not yet compiled for the ESP32 in this environment (no
   arduino-cli) — compile + hardware-test before trusting it. Proposal updated to **v3.4** (`make_proposal.js`, PNGs in `proposal/images/` re-rendered).

## What it is (firmware 2.4.0; the bullets below still describe 2.3.1 where history item 7 does not override them)
- **Sensor pins/defaults live in `sensors_config.h`** (new, `#ifndef`-guarded). `config.h` holds the user's real WiFi password:
  **never edit, print or copy it**. Its old sensor names (`PIN_US_*`, `PIN_IR_CAR_*`, `IR_ACTIVE_LOW`, `CAMEL_CM`, `USE_CAMEL_*`,
  `US_CAL_*`) are stale/unused; `US_MIN_CM`, `US_MAX_CM`, `US_FAULT_READINGS`, `CONFIRM_SAMPLES`, `HOLD_SEC`, … are still used.
- **Camel gate:** IR A = D13 (side-A edge), IR B = D14 (side-B edge), FC-51, VCC 3.3 V, active LOW (`CAMEL_IR_ACTIVE_LOW`,
  INPUT_PULLUP). PIR = D4 (HC-SR501: VCC 5 V, OUT 3.3 V; `PIR_ACTIVE_HIGH`, INPUT_PULLDOWN). Sampled every 50 ms (`sampleGate()`).
- **Vehicle sensors:** 2× HC-SR04, A TRIG D32 / ECHO D33, B TRIG D26 / ECHO D27, VCC 5 V, **ECHO through 1 kΩ / 2 kΩ**.
  Beside each lane at the "400 m" point (before the 300 m sign), pinging across it. Car passing = median reading ≤ `carCm`
  ("Detection Limit (Lane Width)" on the page; empty lane = nothing in range = "Lane Empty"). Approaching for `approachSec`
  (default `VEH_APPROACH_SEC` 8 s in sensors_config.h — config.h's APPROACH_SEC 3 is no longer used for the default).
- **Power: 2× INA219 on I2C** (SDA D17, SCL D16, `Wire`, no library): #1 at 0x40 on the solar panel (VIN+ panel, VIN− charger), #2 at 0x41
  (A0 bridged) between the battery node and the cell (VIN+ node, VIN− cell +). Positive current = panel → charger / charging.
  Chain: panel → INA#1 → TP4056 (with protection) → node → INA#2 → 18650; MT3608 (5.1 V) from the node → VIN.
  mA = shunt reg × 10 / `INA_SHUNT_MOHM` (0.1 Ω), bus V = reg >> 3 × 4 mV; read every 250 ms, EMA 0.3. `INA_*_INVERT` flips a sign.
- Outputs (2.4.0): **one 74HC595** (`ledsWrite(uint8_t)`): Q0 yellow lamp (`Q_LAMP`), Q1–Q3 warning signs 300/200/100 m, Q4 camel sign,
  Q5–Q7 spare (`LED_COUNT` 5, `SIGNS_MASK`). Each LED keeps its own 220 Ω, two LEDs (A + B) per output. Buzzer D25 (NPN). Status LED D2. Free: D21/D22, D34, D35, VP, VN.
- States: WARMUP (3 s), CLEAR, WARNING (camel or manual alert), DANGER (hazard + vehicle sensor fired within `approachSec`).
  2.4.0 leaves `Settings.pirSec/usePir/flashSec/amberSec` in the struct unused (layout kept, SETTINGS_VERSION still 5); the telemetry no longer sends
  `pir, camelPend, unconfCount/Dir, sigLeft` or `cfg.pirSec/usePir/flashSec/amberSec`; `sig` = `flash` (`on` during the lamp test), `signA/signB` = `signW`.
  Output test `leds` runs Q0–Q4 (5 s). Faults: `PIR` gone.
  CAUTION is still in the enum but unused. **Decision on the ESP32 only**; the dashboard monitors/logs/configures.

## Layout
| Path | Notes |
|---|---|
| `firmware/camel_guard_esp32/` | `.ino` v2.3.1, **SETTINGS_VERSION 5** (`carCm`, `pirSec`, `usePir` appended at the END of `Settings`; `camelCm` stays as an unused field so the layout is unchanged; v4 and v3 layouts are converted in `loadSettings()`, older ones use the defaults). `config.h` + `sensors_config.h`. No extra libraries. Compiles clean: 86 % flash, 17 % RAM (core 3.3.8). |
| `dashboard/server.py` | stdlib only. Port 5100, UDP discovery 45811 (`CAMELGUARD_DISCOVER` / `CAMELGUARD_HERE:ip:port`). DB `dashboard/data/road_log.db`: `hazards`, `vehicles`, `events`, `team`, `devices`. Seeds the team from the proposal on first start. |
| `dashboard/static/index.html` | Copied from the fire-alarm page and adapted. Live road view = one SVG built once by `buildRoad()`, then `updateRoad()` only changes attributes/classes (keeps CSS blink animations running). Camel gate: IR beams `gbA/gbB` + boxes `irA/irB`, PIR `pirS` + fan `pirF`, one camel `fig` (moved by `transform` to the IR that sees it), texts `gAt/gBt`; car HC-SR04 `usCA/usCB(2)` at the far left / right (400 m points, before the 300 m signs) + texts `dAt/dBt`, cars `carA/carB` just past them. Signs per side from `signA/signB` (fallback `signW`/`signC`). **Scene is mirror-symmetric around x = 500 of the 1000-wide viewBox** (user, 2026-10-18): gate 462–538, signals A 420 / B 580, camel signs 360 / 640, warnings A 125/205/285 ↔ B 875/795/715, car sensors 36 / 964, buzzer 600 — keep it symmetric when adding things. No "–" placeholders in the SVG texts when there is no data. `?snapshot` disables SSE (for headless-Edge screenshots). |
| `dashboard/simulator.py` | Mirrors the firmware logic with a virtual clock (50 ms steps): gate IR/PIR confirmation, median filter, faults. Scenarios `auto` (has an `ironly` scene: IR A without PIR that must be ignored), `clear`, `camel` (from A, later from B), `danger`, `multi`. MACs `5A:17:00:00:01:0n`. |
| `tools/` | `project_settings.py` (ini `camel_guard.ini`, firewall rule "Camel Guard Dashboard"), `firmware_tool.py` (LIBS empty, build dir `%TEMP%\camel_guard_build`), `setup_config.py`, `make_wiring.py` (→ `docs/wiring_diagram.svg`, 1640×1540; PIR box right of the board, wire from D4 hops over the I2C lanes), `make_diagrams.py` (→ `docs/block_diagram.svg`, `docs/state_machine.svg`). |
| `docs/` | the three SVGs above, `UJI_HARDWARE.id.md`. |
| `proposal/` | Original draft docx, older generated versions, current **`Camel_Crossing_Highway_Safety_Proposal_v3.4.docx`** (`node make_proposal.js <root> <file>` with docx-js; docx is installed in `..\Gut It Checked Out\node_modules` — run with `NODE_PATH` pointing there), `images/` (PNG renders of the SVGs via headless Edge; dashboard screenshot from `http://localhost:5100/?snapshot#realtime` while the simulator ran). |
| `backup_pre_2.3.0/`, `backup_pre_2.3.1/` | copies of every file changed for 2.3.0 / 2.3.1 (no git). |

## Invariants — keep in sync
- Settings keys (firmware `Settings`/`applyConfig`, `CONFIG_LIMITS`/`CONFIG_FLAGS` in server.py, `DEFAULT_CFG`/`LIMITS`/`FLAGS`
  in simulator.py, `name=` of `form[data-cfg]` inputs, `fillSettings()` key lists and the flag list in the page's submit handler):
  `carCm (3–150 cm, default 15), confirm (1–20 × 50 ms, IR), pirSec (1–10 s, default 3), holdSec (2–60), approachSec (1–20, default 8),
  flashSec (1–10), amberSec (1–10)` + flags `buzzer, useA, useB, usePir` (useA/B = camel IR fitted) + `name`.
- **Camel confirmation (firmware `sampleGate()` = simulator `sample_gate()`):** IR seen = blocked `confirm` readings in a row;
  a new IR edge while no camel and nothing pending → `pendSide`. Confirmed when PIR was high at any time from `pirSec` before
  the trigger until now (`!usePir` → immediately); direction `camelDir` = pending side; `camelCount++`. No PIR within `pirSec`
  → `unconfCount++`, `unconfDir`, dropped. Held while an IR is seen or the PIR is high, then `holdSec`. Triggers during a camel
  are the same camel (not counted, not logged).
- **Vehicle HC-SR04 path (firmware = simulator):** pinged every 50 ms (A and B 25 ms apart), echo timed by an interrupt
  (`echoIsr`, sets `sawRise`/`gotEcho`, `INPUT_PULLDOWN` on ECHO). Reading = cm (2–200), `FAR_CM` 999 (pulse but nothing in range,
  or still in a long clone timeout), or −1 (no edge at all = dead). Median of the last 3 (FAR included); 3 dead → dist unknown.
  Vehicle = `0 < dist ≤ carCm`, counted on the rising edge with `VEH_DEBOUNCE_MS` (300 ms); approaching = within `approachSec`.
- Faults (telemetry `faults`, trailing-space separated): `US-x` vehicle HC-SR04 dead for 20 readings, `CAR-x` "vehicle" for 2 min,
  `OBJ-x` camel IR blocked 2 min, `PIR` PIR high 2 min (`GATE_STUCK_MS`), plus power `BAT-LOW`, `INA-S`, `INA-B`. Explained by
  `faultText()` in the page (FAULT_TEXT entries are functions of the side letter). Buffer 80 chars.
- **Power telemetry** (no new settings, no NVS change): `pvOk, batOk` (module answering), `sun` (panel ≥ `SUN_MIN_V` 4.5 V), `pvV, pvmA, batV, batmA`,
  `batPct` (−1 = no battery sensor; table in `batPercent()` = `v_to_pct()` in the simulator), `chg` = `none / charging (> +20 mA) /
  discharging (< −20 mA) / full (≥ 4.10 V, no current) / idle`, `vbat` = batV (kept for old readers). A module that was never seen = not
  fitted (page hides the Power box, no fault); one that was seen and went silent for 5 reads = fault `INA-S` / `INA-B`; battery below
  `BAT_LOW_V` 3.45 V until `BAT_OK_V` 3.60 V = `BAT-LOW` in `faults` (the server turns it into a "Battery low" event, not a sensor fault).
  The server keeps `batV, batmA, pvV, pvW` in the 10-minute history for four extra charts and logs charging / full / on-battery changes.
- **Traffic signals follow the UAE sequence (user's requirement, 2026-09-23):** solid green → flashing green `flashSec`
  (default 3 s, 1 Hz) → amber `amberSec` (3 s) → red, held while WARNING/DANGER and at least `SIGNAL_MIN_RED_MS` (2 s),
  then straight to green. Never skipped: once started it always runs to red; DANGER does not jump to red (signs fast +
  buzzer instead). WARMUP (phase green) shows steady amber. Output test "signals" loops green 2 s → flash → amber →
  red 2 s from `testStartMs`; the page always sends at least one full cycle (4 + flashSec + amberSec s) for it —
  with the old 5 s default the test ended during flashing green, so amber/red never showed (user report 2026-09-28).
  `updateSignalPhase()` in the firmware = `Device.signal_phase()` in the simulator.
- Telemetry fields the page uses: `state, hazard, hazardSec, camel (bool), camelDir ("A"/"B"/""), camelPend (IR side waiting
  for PIR, ""), irA, irB, pir (raw now), distA/distB (cm, −1 = nothing in range / no echo), carA/carB (vehicle in front now),
  vehA, vehB, veh, vehCount, vehCountA, vehCountB, lastDir, camelCount, unconfCount, unconfDir, sig (green / green-flash / amber /
  red), sigLeft, signC / signW (off / flash / fast, overall), signA / signB (per direction: flash, or fast while that side's car is
  approaching), buzz, faults, led`. Firmware: `SIDE_SIGNS(side)` mask in `updateOutputs()` = simulator `outputs()`. Hazard kinds: `camel`, `manual`.
  Server history points: `camel, pir, veh, lvl` (+ power). Server events: hazard detail "from side X (IR X + PIR)",
  "Camel sensor: IR without PIR — ignored" when `unconfCount` grows (reset on device restart).
- Commands: reset, config, wifi, reboot, factory, alert, test (outputs `buzzer, signals, signs, leds`). **`calibrate` was
  removed in 2.3.0** (no empty-road baseline any more; NVS keys `baseA/baseB` of 2.1/2.2 are left unused).
- Output test `leds`: one 74HC595 output at a time, 1 s each (bits 0–6, 8–14), telemetry `led` = bit number (else −1);
  the page names it ("Chip #1 Q3 (pin 3) — Warning sign 300 m A") and runs ≥ 14 s. Added because on the user's board the
  "signs" test lit the traffic-signal LEDs — the mapping problem was still open (2026-09-28).
- A real hazard cancels an output test; tests are refused during WARNING/DANGER (server 409 + firmware).
- `ledsWrite()` uses 1 µs edges and `updateOutputs()` forces a rewrite every 500 ms (firmware 2.0.1, 2026-09-28):
  on the user's breadboard the LEDs came up random; a glitch must not stay latched.
- Arduino builder puts auto-prototypes before the first function: `struct CarSensor` / `GateIr` must stay
  defined above `readMac()`.
- `.bat` files must be CRLF + ASCII.
- **Cloud mode (2026-10-19, user chose Render):** `render.yaml` + `docs/DEPLOY_CLOUD.id.md`. `RENDER`/`CG_CLOUD=1` → server refuses
  to start without `DASHBOARD_PASSWORD` (HTTP Basic on every path except `/healthz` and `/api/telemetry`), skips UDP discovery;
  `DEVICE_KEY` env → telemetry needs header `X-Device-Key`. Env vars only, never in git. Firmware: port 443 → `https://` with
  `WiFiClientSecure.setInsecure()`, 6 s timeouts; `DEVICE_KEY` (sensors_config.h default "", the user defines it in config.h).
  Page "Dashboard Address" accepts `host:port` (server splits it, strips `https://`). Local mode (no env vars) unchanged.

## Status (2026-10-18)
- Firmware 2.3.1 compiled clean (86 % flash, 17 % RAM). Simulator check of 2.3.1: one-sided DANGER (car only at B → B signs
  fast, A flashing), events name the car's side, cars without a camel only counted. Proposal v3.3 generated.
- Firmware 2.3.0 compiled clean (86 % flash, 17 % RAM, no warnings). Verified with the simulator on an isolated copy
  (port 5170): camel A→B and B→A confirmed with the right side, counted once; IR-only trigger ignored + logged; cars counted
  per side from the HC-SR04 distance; DANGER with a vehicle; config clamps (carCm 500 → 150, pirSec 0 → 1, old `camelCm` dropped);
  usePir=false → IR alone confirms; fault tokens US/CAR/OBJ/PIR; live view, tiles, logic panel, Sensors tab, Overview render
  without console errors. Wiring / block / state SVGs regenerated, proposal v3.2 generated.
- The user's own dashboard on 5100 must be restarted (START_DASHBOARD.bat) to load the new `server.py` / page.
- **Not yet tested on real hardware with this firmware** — rewire per `docs/UJI_HARDWARE.id.md` Tahap 0 (HC-SR04 to the
  approaches, IR modules to the camel gate, new PIR on D4).

## Coding rules — Karpathy guidelines (user, 2026-10-18; from github.com/multica-ai/andrej-karpathy-skills)
Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
