/*
 * Camel Guard — sensor pins and defaults since firmware 2.3.0
 * ------------------------------------------------------------
 * Sensor layout 2.3.0:
 *   - VEHICLE sensors = 2x HC-SR04 ultrasonic, one per approach (side A / side B) at the "400 m" point, before
 *                       the 300 m warning sign. Mounted BESIDE the lane, pinging ACROSS it: a pass-by presence
 *                       detector (a car passing that point), not a distance-to-the-crossing measurement.
 *   - CAMEL sensors   = 2x IR obstacle module (FC-51, digital OUT) at the camel gate, one at the side-A edge and
 *                       one at the side-B edge of the crossing. Since 2.4.0 there is NO PIR: an IR trigger alone
 *                       is a camel.
 *   - OUTPUTS         = one 74HC595 (Q0 yellow lamp, Q1..Q3 warning signs 300/200/100 m, Q4 camel sign);
 *                       side A and side B are wired in parallel.
 *
 * The older sensor names in config.h (PIN_US_A_TRIG, PIN_IR_CAR_A, CAMEL_CM, USE_CAMEL_A, ...) are no longer used:
 * these are the ones the firmware reads. config.h stays as it is (it holds the WiFi settings).
 * Every value can be overridden by defining it in config.h first.
 */
#pragma once

// ---- vehicle sensors: HC-SR04 (VCC 5 V). ECHO is a 5 V signal: ALWAYS through a divider
//      ECHO -> 1 kOhm -> GPIO, GPIO -> 2 kOhm -> GND  (5 V x 2/3 = 3.3 V), or a level shifter.
//      Same pins as the old camel HC-SR04, so the dividers already on the board stay.
#ifndef PIN_CAR_A_TRIG
#define PIN_CAR_A_TRIG   32        // vehicle sensor, traffic coming from side A: TRIG (output, 3.3 V is enough)
#define PIN_CAR_A_ECHO   33        //                                              ECHO (through the divider)
#define PIN_CAR_B_TRIG   26        // vehicle sensor, traffic coming from side B: TRIG
#define PIN_CAR_B_ECHO   27        //                                              ECHO (through the divider)
#endif

// ---- camel gate: 2x IR obstacle module (VCC 3.3 V, OUT active LOW). Same pins as the old IR vehicle sensors (D13 / D14).
#ifndef PIN_CAMEL_IR_A
#define PIN_CAMEL_IR_A   13        // camel IR sensor at the side-A edge of the crossing
#define PIN_CAMEL_IR_B   14        // camel IR sensor at the side-B edge of the crossing
#endif
#ifndef CAMEL_IR_ACTIVE_LOW
#define CAMEL_IR_ACTIVE_LOW true   // FC-51 modules pull OUT low when they see something
#endif

// ---- defaults of the settings the dashboard can change (stored in NVS)
#ifndef CAR_CM
#define CAR_CM           15        // detection limit across the lane (cm): a reading at or below this = a car passing
                                   // (set it a little below the lane width; nothing in range = empty lane)
#endif
#ifndef VEH_APPROACH_SEC
#define VEH_APPROACH_SEC 8         // after a car passed the 400 m point it counts as approaching (that side's signs flash
                                   // fast, DANGER if a camel is at the gate) this long = its travel time to the crossing
#endif
#ifndef PIR_SEC
#define PIR_SEC          3         // unused since 2.4.0 (no PIR); only fills the legacy NVS field
#endif
#ifndef USE_PIR
#define USE_PIR          false     // unused since 2.4.0 (no PIR); only fills the legacy NVS field
#endif
#define LIMIT_CAR_MIN    3         // limits the dashboard may set (applyConfig clamps to these)
#define LIMIT_CAR_MAX    150
#define LIMIT_PIR_MIN    1
#define LIMIT_PIR_MAX    10

// ---- fixed timing
#ifndef VEH_DEBOUNCE_MS
#define VEH_DEBOUNCE_MS  300       // one car stays in front of its sensor for a while: ignore re-triggers
#endif
#ifndef CAR_US_SAMPLES
#define CAR_US_SAMPLES   3         // median of the last 3 pings (one wrong echo is removed)
#endif
// ---- cloud dashboard (docs/DEPLOY_CLOUD.id.md). Leave empty for the PC dashboard on the LAN. For a cloud dashboard,
//      define DEVICE_KEY in config.h (it is not committed to git), with the same value as DEVICE_KEY on Render:
//      #define DEVICE_KEY "your-random-key"
#ifndef DEVICE_KEY
#define DEVICE_KEY       ""
#endif

#ifndef GATE_STUCK_MS
#define GATE_STUCK_MS    120000UL  // camel IR blocked / vehicle sensor "car" this long = fault (OBJ / CAR)
#endif
