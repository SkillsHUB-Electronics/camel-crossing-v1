/*
 * Camel Crossing Highway Safety — ESP32 (Arduino IDE)          "Camel Guard"
 * ---------------------------------------------------------------
 * MINIATURE SIMULATOR: a table-top highway model with toy camels and toy cars.
 * Not a device for a real road. There are no pedestrians on the highway.
 *
 * Sensors : camel gate at the crossing = 2x IR obstacle sensor (FC-51, digital, active LOW), one at the
 *             side-A edge and one at the side-B edge. A camel = an IR trigger confirmed for `confirm` readings;
 *             the side whose IR fired first is the side the camel comes from. (No PIR since 2.4.0.)
 *           2x HC-SR04 ultrasonic = vehicle sensors, one per approach at the "400 m" point (before the
 *             300 m warning sign), beside the lane and pinging ACROSS it: a pass-by presence detector,
 *             reading <= carCm (median of 3 pings) = a car passing that point. It does not measure how far
 *             the car is from the crossing.
 *           Pins and defaults of the sensors: sensors_config.h
 * Outputs : ONE 74HC595 (the two sides are wired in parallel): Q0 = yellow caution lamp at the crossing
 *             (always flashing, camel or not), Q1..Q3 = warning signs 300 m / 200 m / 100 m (same output for both
 *             directions), Q4 = camel-crossing sign; buzzer at the crossing. The signs flash while a camel is
 *             crossing, fast (4 Hz) when a car is coming too.
 * Power   : solar panel + Li-ion battery, measured by two INA219 on I2C: battery %, charge state, solar V / A,
 *             low-battery warning (modules that are not fitted are skipped)
 * Logic   : IR confirmation + direction + hold time + state machine (edge decision)
 * Network : sends JSON telemetry to the PC dashboard every second (HTTP POST);
 *           the response carries one command (reset / config / wifi / alert / test / ...).
 *
 * The warning decision is made ON THE ESP32, so signals, signs and buzzer keep
 * working even if WiFi, the access point or the PC is down.
 * The dashboard is for monitoring, logging and settings.
 *
 * Settings live in the ESP32's own flash (NVS) and survive a power cycle:
 *   - up to MAX_NETWORKS WiFi networks, tried in order
 *   - the dashboard address (or empty: found by UDP broadcast)
 *   - device name, vehicle distance, IR confirmation, hold time
 * If no saved network answers, the device starts the access point AP_SSID and
 * serves a small setup page at http://192.168.4.1 — so changing networks never
 * requires a firmware re-upload.
 *
 * Wiring : see docs/wiring_diagram.svg
 * Board  : "ESP32 Dev Module" (Boards Manager: esp32 by Espressif, core 2.x or 3.x)
 * Library: none — WiFi, WebServer, HTTPClient and Preferences are part of the ESP32 core.
 */
#include <WiFi.h>
#include <WiFiUdp.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <Preferences.h>
#include <Wire.h>
#include <esp_mac.h>
#include <esp_system.h>
#include "config.h"
#include "sensors_config.h"

#define FW_VERSION "2.4.0"
#define SETTINGS_VERSION 5   // 5 = + carCm, pirSec, usePir; 4 and 3 are converted, older ones use the defaults (pirSec/usePir/flashSec/amberSec unused since 2.4.0, layout kept)

enum RoadState : uint8_t { ST_WARMUP, ST_CLEAR, ST_CAUTION, ST_WARNING, ST_DANGER };   // CAUTION is not used any more
static const char *STATE_NAMES[] = {"WARMUP", "CLEAR", "CAUTION", "WARNING", "DANGER"};

// Output of the single 74HC595 (see config.h): bit = Q number. Side A and side B are wired in parallel.
#define Q_LAMP   0          // yellow caution lamp at the crossing (always flashing)
#define Q_W300   1          // advance warning sign 300 m before the crossing
#define Q_W200   2
#define Q_W100   3
#define Q_CAMEL  4          // "camel crossing" sign at the crossing
#define LED_COUNT 5         // Q5..Q7 are spare
#define WARN_MASK ((uint8_t)((1u << Q_W300) | (1u << Q_W200) | (1u << Q_W100)))
#define SIGNS_MASK ((uint8_t)(WARN_MASK | (1u << Q_CAMEL)))

// =====================================================================
//  Settings stored in NVS
//  Scalars are read by the detection loop (core 1) and written by the network
//  task (core 0). Aligned 32-bit reads/writes are atomic on the ESP32, and a
//  setting that changes between two evaluations is harmless, so no lock is
//  needed here. The strings are only ever touched by the network task.
// =====================================================================
struct Settings {
  uint8_t  ver;
  char     name[25];
  uint8_t  confirm;              // a camel IR sensor must be blocked this many readings in a row (50 ms each)
  uint16_t holdSec;              // the warning stays on this long after the camel was last seen
  uint8_t  approachSec;          // a vehicle counts as "approaching" this long after its sensor fired
  uint8_t  flashSec, amberSec;   // UAE signal sequence
  bool     useA, useB;           // camel IR sensors A / B fitted
  bool     buzzer;
  char     host[41];
  uint16_t port;
  uint8_t  nNets;
  char     ssid[MAX_NETWORKS][33];
  char     pass[MAX_NETWORKS][64];
  uint8_t  camelCm;              // v4 (HC-SR04 camel height) — unused since 2.3.0, kept so the layout stays
  uint8_t  carCm;                // v5: a vehicle sensor reading at or below this (cm) = a vehicle
  uint8_t  pirSec;               // v5: an IR trigger needs PIR motion within this many seconds
  bool     usePir;               // v5: PIR fitted (false = IR alone confirms)
  // Add new fields at the END and bump SETTINGS_VERSION, with a migration in loadSettings().
};
static Settings S;
static Preferences prefs;

// Sensor records. Defined before the first function: the Arduino builder puts its generated
// prototypes there, and sampleCar(CarSensor &) / sampleGateIr(GateIr &) need the types.
struct CarSensor {        // one HC-SR04 on an approach (vehicle sensor)
  uint8_t trig, echo;
  char tag;                 // 'A' / 'B'
  volatile uint32_t riseUs; // echo timing, written by the interrupt
  volatile uint32_t widthUs;
  volatile bool gotEcho;    // falling edge seen since the last trigger
  volatile bool sawRise;    // rising edge seen since the last trigger
  bool pending;             // a ping is under way
  float hist[CAR_US_SAMPLES]; // last readings (median removes single wrong echoes); FAR_CM = nothing in range
  uint8_t hi, hn;
  uint8_t deadN;            // readings in a row without ANY echo pulse (sensor or wiring fault)
  float dist;               // filtered distance (cm): 0 = unknown, FAR_CM = nothing in range
  bool car;                 // a vehicle is in front of / under the sensor now (dist <= carCm)
  uint32_t carSinceMs;      // for the "always a car" fault
  uint32_t lastHitMs;       // last counted vehicle (0 = none yet)
  uint32_t count;
};
struct GateIr {           // one IR obstacle module at the camel gate
  uint8_t pin;
  char tag;
  bool raw;                 // blocked right now
  uint8_t blockedN;         // blocked readings in a row
  bool seen;                // blocked for at least `confirm` readings
  uint32_t seenMs;          // last time it was seen
  uint32_t blockedSinceMs;  // for the "object left in the gate" fault
};
struct Ina {                // one INA219 voltage / current sensor on the I2C bus
  uint8_t addr;
  bool present;             // answers right now
  bool everSeen;            // answered at least once since boot: missing = not fitted, vanished = fault
  uint8_t fails;            // reads in a row that failed
  bool valid;               // v / ma hold a measurement
  float v, ma;              // smoothed bus voltage (V) and current (mA)
  uint32_t probeMs;         // last look for a sensor that is not there
};

// The chip's factory MAC (eFuse) — unique per board and available before WiFi starts.
// The dashboard uses it to tell several crossings apart, so names can be changed freely.
static uint8_t chipMac[6];
static char macText[18] = "";

// Setup access point name, unique per board ("CamelGuard-Setup-CEE4").
static char apName[33] = AP_SSID;

static void readMac() {
  esp_efuse_mac_get_default(chipMac);
  snprintf(macText, sizeof(macText), "%02X:%02X:%02X:%02X:%02X:%02X",
           chipMac[0], chipMac[1], chipMac[2], chipMac[3], chipMac[4], chipMac[5]);
  snprintf(apName, sizeof(apName), "%s-%02X%02X", AP_SSID, chipMac[4], chipMac[5]);
}

// Why the chip last started — reported to the dashboard so a crash or a power dip is visible.
static const char *bootReason = "unknown";
static void readBootReason() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:   bootReason = "power on"; break;
    case ESP_RST_EXT:       bootReason = "reset pin"; break;
    case ESP_RST_SW:        bootReason = "software restart"; break;
    case ESP_RST_PANIC:     bootReason = "crash (panic)"; break;
    case ESP_RST_INT_WDT:   bootReason = "interrupt watchdog"; break;
    case ESP_RST_TASK_WDT:  bootReason = "task watchdog"; break;
    case ESP_RST_WDT:       bootReason = "watchdog"; break;
    case ESP_RST_BROWNOUT:  bootReason = "brownout (supply voltage dropped)"; break;
    case ESP_RST_DEEPSLEEP: bootReason = "deep sleep wake"; break;
    default:                bootReason = "unknown"; break;
  }
}

static void defaultSettings(Settings &s) {
  memset(&s, 0, sizeof(s));
  s.ver = SETTINGS_VERSION;
  // DEVICE_ID + last 4 hex digits of the MAC, e.g. "camel-guard-C334": unique on a fresh board.
  snprintf(s.name, sizeof(s.name), "%s-%02X%02X", DEVICE_ID, chipMac[4], chipMac[5]);
  s.confirm = CONFIRM_SAMPLES;
  s.camelCm = CAMEL_CM;
  s.carCm = CAR_CM;
  s.pirSec = PIR_SEC;
  s.usePir = USE_PIR;
  s.holdSec = HOLD_SEC;
  s.approachSec = VEH_APPROACH_SEC;
  s.flashSec = SIGNAL_FLASH_SEC;
  s.amberSec = SIGNAL_AMBER_SEC;
  s.useA = USE_CAMEL_A;
  s.useB = USE_CAMEL_B;
  s.buzzer = BUZZER_ENABLED;
  strlcpy(s.host, SERVER_HOST, sizeof(s.host));
  s.port = SERVER_PORT;
  if (strlen(WIFI_SSID) && strcmp(WIFI_SSID, "YOUR_WIFI_SSID") != 0) {
    strlcpy(s.ssid[0], WIFI_SSID, sizeof(s.ssid[0]));
    strlcpy(s.pass[0], WIFI_PASS, sizeof(s.pass[0]));
    s.nNets = 1;
  }
}

static void saveSettings() {
  prefs.putBytes("cfg", &S, sizeof(S));
}

static void loadSettings() {
  defaultSettings(S);
  prefs.begin("camelguard", false);
  size_t len = prefs.getBytesLength("cfg");
  static uint8_t raw[sizeof(Settings)];
  if (len == sizeof(Settings) && prefs.getBytes("cfg", raw, len) == len && raw[0] == SETTINGS_VERSION) {
    memcpy(&S, raw, sizeof(Settings));
    if (S.flashSec < 1 || S.flashSec > 10) S.flashSec = SIGNAL_FLASH_SEC;
    if (S.amberSec < 1 || S.amberSec > 10) S.amberSec = SIGNAL_AMBER_SEC;
    if (S.carCm < LIMIT_CAR_MIN || S.carCm > LIMIT_CAR_MAX) S.carCm = CAR_CM;
    if (S.pirSec < LIMIT_PIR_MIN || S.pirSec > LIMIT_PIR_MAX) S.pirSec = PIR_SEC;
    Serial.printf("[CONFIG] Loaded from flash: %u WiFi network(s), name \"%s\"\n", S.nNets, S.name);
    return;
  }
  // Older layouts are the same fields with fewer at the end: keep what they have, the new fields keep
  // their defaults (S already holds them). v4 = firmware 2.1.x / 2.2.x, v3 = firmware 2.0.x.
  const size_t v4Len = offsetof(Settings, carCm), v3Len = offsetof(Settings, camelCm);
  uint8_t oldVer = 0;
  size_t keep = 0;
  if (len >= v4Len && len <= v4Len + 1) { oldVer = 4; keep = v4Len; }
  else if (len >= v3Len && len <= v3Len + 1) { oldVer = 3; keep = v3Len; }
  if (oldVer && prefs.getBytes("cfg", raw, len) == len && raw[0] == oldVer) {
    memcpy(&S, raw, keep);
    S.ver = SETTINGS_VERSION;
    if (S.flashSec < 1 || S.flashSec > 10) S.flashSec = SIGNAL_FLASH_SEC;
    if (S.amberSec < 1 || S.amberSec > 10) S.amberSec = SIGNAL_AMBER_SEC;
    saveSettings();
    Serial.printf("[CONFIG] Settings of firmware %s kept (%u WiFi network(s), name \"%s\"); vehicle <= %u cm, PIR window %u s\n",
                  oldVer == 4 ? "2.1.x / 2.2.x" : "2.0.x", S.nNets, S.name, S.carCm, S.pirSec);
    return;
  }
  // Settings of firmware 1.x do not fit any more: start from config.h.
  if (len) Serial.println("[CONFIG] Stored settings are from an older layout — using the defaults from config.h");
  else Serial.println("[CONFIG] No settings in flash yet — using the defaults from config.h");
  saveSettings();
}

static inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

// =====================================================================
//  Snapshot shared with the network task
// =====================================================================
struct Snapshot {
  uint8_t state;
  char hazard[40];
  uint32_t hazardSec;
  bool camel;                    // camel confirmed at the gate (and held)
  char camelDir[2];              // side the current / last camel came from ("A" / "B" / "")
  bool irA, irB;                 // camel gate sensors right now
  float distA, distB;            // vehicle HC-SR04 distance now (cm, 0 = unknown, FAR_CM = nothing in range)
  bool carA, carB;               // vehicle in front of the sensor right now
  bool vehA, vehB;               // vehicle approaching from side A / B
  uint32_t vehCount, vehCountA, vehCountB, camelCount;
  char lastDir[2];
  bool pvOk, batOk, sun;         // INA219 on the panel / battery answering; sun above SUN_MIN_V
  float pvV, pvmA, batV, batmA;  // solar panel and battery measurements
  int8_t batPct;                 // battery charge in % (about), -1 = no battery sensor
  char chg[12];                  // none / charging / full / discharging / idle
  char faults[80];
  char sig[12], signC[6], signW[6], signA[6], signB[6], buzz[6];   // sig = yellow lamp, signA/B = signW (paralleled)
};
static Snapshot snap;
static portMUX_TYPE snapMux = portMUX_INITIALIZER_UNLOCKED;
static volatile bool resetRequested = false;
static volatile bool wifiOnline = false;
// "Alert all crossings" from the dashboard: the signs flash at every
// crossing, even though THIS crossing detected nothing (e.g. a herd reported further along the road).
// Cleared by CLEAR on the dashboard.
static volatile bool alertOn = false;

// Output test from the dashboard (Settings -> Output Test). A real hazard always wins: it
// cancels the test, so a test can never hide a camel on the road.
enum TestOut : uint8_t { T_NONE, T_BUZZER, T_SIGNALS, T_SIGNS, T_LEDS };
static const char *TEST_NAMES[] = {"none", "buzzer", "signals", "signs", "leds"};
static volatile uint8_t testOut = T_NONE;
static volatile uint32_t testUntilMs = 0, testStartMs = 0;

static void writeOut(uint8_t pin, bool on, bool activeHigh) {
  digitalWrite(pin, (on == activeHigh) ? HIGH : LOW);
}

// =====================================================================
//  Vehicle sensors: one HC-SR04 ultrasonic sensor per approach at the "400 m" point (before the 300 m sign),
//  beside the lane and pinging across it: it only tells that a car is passing that point. Each sensor is pinged
//  every 50 ms (A and B 25 ms apart, so they cannot hear each other); the echo is timed by an interrupt, so the
//  loop never waits. reading = median of the last 3 pings; reading <= carCm = a car passing. "Nothing in range" is a normal
//  reading (the empty lane may give no echo); NO echo pulse at all for 1 s = fault US-A / US-B (wiring).
// =====================================================================
#define FAR_CM 999.0f            // "nothing in range" inside the median filter
static CarSensor uA = {PIN_CAR_A_TRIG, PIN_CAR_A_ECHO, 'A'}, uB = {PIN_CAR_B_TRIG, PIN_CAR_B_ECHO, 'B'};
static CarSensor *const cars[2] = {&uA, &uB};
static uint32_t vehCount = 0;
static char lastDir[2] = "";

// ECHO stays high for as long as the sound needs to travel there and back.
static void IRAM_ATTR echoIsr(void *arg) {
  CarSensor *c = (CarSensor *)arg;
  if (gpio_get_level((gpio_num_t)c->echo)) { c->riseUs = micros(); c->sawRise = true; }
  else { c->widthUs = micros() - c->riseUs; c->gotEcho = true; }
}

static void pingStart(CarSensor &c) {
  c.gotEcho = false;
  c.sawRise = false;
  digitalWrite(c.trig, LOW);
  delayMicroseconds(2);
  digitalWrite(c.trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(c.trig, LOW);
  c.pending = true;
}

// Result of the previous ping: distance in cm, FAR_CM = the sensor answered but nothing is in range
// (or it timed out), -1 = no echo pulse at all (sensor missing, no 5 V, ECHO / divider not connected).
static float pingResult(CarSensor &c) {
  if (!c.pending) return -1;
  c.pending = false;
  bool high = digitalRead(c.echo) == HIGH;           // a clone HC-SR04 may still be in its long timeout pulse
  if (!c.gotEcho && !c.sawRise) return high ? FAR_CM : -1;
  if (!c.gotEcho || !c.sawRise) return FAR_CM;       // pulse started before / still running: nothing near
  float cm = c.widthUs / 58.0f;
  return (cm >= US_MIN_CM && cm <= US_MAX_CM) ? cm : FAR_CM;
}

static float medianOf(const float *v, uint8_t n) {
  float s[CAR_US_SAMPLES];
  for (uint8_t i = 0; i < n; i++) {                  // insertion sort of a copy
    float x = v[i];
    int8_t j = i - 1;
    while (j >= 0 && s[j] > x) { s[j + 1] = s[j]; j--; }
    s[j + 1] = x;
  }
  return s[n / 2];
}

// One reading of one vehicle sensor (every 50 ms per sensor): collects the previous echo, starts the next ping.
static void sampleCar(CarSensor &c, uint32_t now) {
  float r = pingResult(c);
  pingStart(c);
  if (r < 0) {
    if (c.deadN < 255) c.deadN++;
    if (c.deadN >= 3) { c.dist = 0; c.hn = 0; }      // 0.15 s without any echo: unknown
  } else {
    c.deadN = 0;
    c.hist[c.hi] = r;
    c.hi = (c.hi + 1) % CAR_US_SAMPLES;
    if (c.hn < CAR_US_SAMPLES) c.hn++;
    c.dist = medianOf(c.hist, c.hn);
  }
  bool car = c.dist > 0 && c.dist < FAR_CM && c.dist <= (float)S.carCm;
  if (car && !c.car && (!c.lastHitMs || now - c.lastHitMs >= VEH_DEBOUNCE_MS)) {
    c.lastHitMs = now;
    c.count++;
    vehCount++;
    strlcpy(lastDir, c.tag == 'A' ? "A" : "B", sizeof(lastDir));
    Serial.printf("[VEHICLE] #%lu approaching from side %c (%.1f cm)\n", (unsigned long)vehCount, c.tag, c.dist);
  }
  if (car) { if (!c.carSinceMs) c.carSinceMs = now; }
  else c.carSinceMs = 0;
  c.car = car;
}

static bool approaching(const CarSensor &c, uint32_t now) {
  return c.lastHitMs && now - c.lastHitMs < (uint32_t)S.approachSec * 1000UL;
}

// =====================================================================
//  Camel gate: IR A at the side-A edge of the crossing, IR B at the side-B edge.
//    1. An IR sensor blocked for `confirm` readings (50 ms each) = camel CONFIRMED, coming from the side
//       that triggered first; counted once.
//    2. The camel is held while an IR is blocked, and holdSec after that.
//       The camel leaving through the other IR is the same camel (not counted again).
// =====================================================================
static GateIr gA = {PIN_CAMEL_IR_A, 'A'}, gB = {PIN_CAMEL_IR_B, 'B'};
static GateIr *const gates[2] = {&gA, &gB};
static uint32_t camelCount = 0;
static bool camelOn = false;            // confirmed camel at the gate (held)
static char camelDir[2] = "";           // side it came from
static uint32_t camelSeenMs = 0;

static void sampleGateIr(GateIr &g, bool enabled, uint32_t now) {
  if (!enabled) { g.raw = g.seen = false; g.blockedN = 0; g.blockedSinceMs = 0; return; }
  g.raw = digitalRead(g.pin) == (CAMEL_IR_ACTIVE_LOW ? LOW : HIGH);
  if (g.raw) {
    if (g.blockedN < 255) g.blockedN++;
    if (!g.blockedSinceMs) g.blockedSinceMs = now;
  } else {
    g.blockedN = 0;
    g.blockedSinceMs = 0;
  }
  bool was = g.seen;
  g.seen = g.blockedN >= S.confirm;
  if (!g.seen) return;
  g.seenMs = now;
  if (was) return;
  // a new trigger on this side. While a camel is confirmed, a new trigger is the same camel
  // walking on (or leaving): not counted again.
  if (!camelOn) {
    camelOn = true;
    camelDir[0] = g.tag; camelDir[1] = '\0';
    camelCount++;
    camelSeenMs = now;
    Serial.printf("[DETECT] Camel CONFIRMED (IR %c), coming from side %c - #%lu\n", g.tag, g.tag, (unsigned long)camelCount);
  }
}

// Every 50 ms: both IR sensors, then the hold logic.
static void sampleGate(uint32_t now) {
  sampleGateIr(gA, S.useA, now);
  sampleGateIr(gB, S.useB, now);
  if (camelOn) {
    if (gA.seen || gB.seen) camelSeenMs = now;
    else if (now - camelSeenMs > (uint32_t)S.holdSec * 1000UL) {
      camelOn = false;
      Serial.println("[DETECT] Camel gate clear");
    }
  }
}

// =====================================================================
//  Road state
// =====================================================================
static RoadState state = ST_WARMUP;
static uint32_t bootMs, hazardStartMs = 0;
static bool camelNow = false, vehNowA = false, vehNowB = false;
static char hazardText[40] = "";
// =====================================================================
//  Power: solar panel + Li-ion battery, measured by two INA219. Current is computed from the shunt
//  voltage (no calibration register needed): mA = raw x 0.01 mV / shunt. Read every 250 ms, smoothed.
// =====================================================================
static Ina inaSolar = {INA_SOLAR_ADDR}, inaBat = {INA_BAT_ADDR};
static float pvV = 0, pvmA = 0, batV = 0, batmA = 0;
static int8_t batPct = -1;
static const char *chgState = "none";
static bool sunNow = false, batLow = false;

static bool inaRead(uint8_t addr, uint8_t reg, int16_t &out) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;           // repeated start
  if (Wire.requestFrom((int)addr, 2) != 2) return false;
  uint8_t hi = Wire.read(), lo = Wire.read();
  out = (int16_t)((hi << 8) | lo);
  return true;
}

static void inaSample(Ina &i, bool invert, uint32_t now) {
  if (!i.present) {
    if (i.probeMs && now - i.probeMs < 10000) return;           // a missing module is looked for every 10 s
    i.probeMs = now;
    Wire.beginTransmission(i.addr);
    if (Wire.endTransmission() != 0) return;
    Wire.beginTransmission(i.addr);                             // config 0x399F: 32 V bus, +-320 mV shunt, 12 bit, continuous
    Wire.write(0x00); Wire.write(0x39); Wire.write(0x9F);
    if (Wire.endTransmission() != 0) return;
    i.present = i.everSeen = true;
    i.fails = 0;
    i.valid = false;
    Serial.printf("[POWER] INA219 at 0x%02X found\n", i.addr);
  }
  int16_t shunt, bus;
  if (!inaRead(i.addr, 0x01, shunt) || !inaRead(i.addr, 0x02, bus)) {
    if (++i.fails >= 5) {
      i.present = i.valid = false;
      Serial.printf("[POWER] INA219 at 0x%02X is not answering\n", i.addr);
    }
    return;
  }
  i.fails = 0;
  float v = ((uint16_t)bus >> 3) * 0.004f;                      // 4 mV per step, bits 15..3
  float ma = shunt * 10.0f / INA_SHUNT_MOHM;                    // 10 uV per step
  if (invert) ma = -ma;
  if (!i.valid) { i.v = v; i.ma = ma; i.valid = true; }
  else { i.v += 0.3f * (v - i.v); i.ma += 0.3f * (ma - i.ma); }
}

// Li-ion cell, resting voltage -> charge in % (rough: the voltage sags under load)
static int8_t batPercent(float v) {
  static const float tv[] = {3.30f, 3.50f, 3.60f, 3.70f, 3.80f, 3.90f, 4.00f, 4.10f, 4.20f};
  static const float tp[] = {0, 6, 12, 25, 45, 65, 80, 90, 100};
  if (v <= tv[0]) return 0;
  for (uint8_t k = 1; k < 9; k++)
    if (v <= tv[k]) return (int8_t)(tp[k - 1] + (tp[k] - tp[k - 1]) * (v - tv[k - 1]) / (tv[k] - tv[k - 1]) + 0.5f);
  return 100;
}

static void powerStep(uint32_t now) {
  inaSample(inaSolar, INA_SOLAR_INVERT, now);
  inaSample(inaBat, INA_BAT_INVERT, now);
  pvV = inaSolar.valid ? inaSolar.v : 0;
  pvmA = inaSolar.valid ? inaSolar.ma : 0;
  sunNow = inaSolar.valid && pvV >= SUN_MIN_V;
  if (inaBat.valid) {
    batV = inaBat.v;
    batmA = inaBat.ma;
    batPct = batPercent(batV);
    chgState = batmA > CHG_MIN_MA ? "charging" : batmA < -CHG_MIN_MA ? "discharging" : batV >= BAT_FULL_V ? "full" : "idle";
    if (!batLow && batV < BAT_LOW_V) { batLow = true; Serial.printf("[POWER] Battery low: %.2f V\n", batV); }
    else if (batLow && batV >= BAT_OK_V) { batLow = false; Serial.println("[POWER] Battery recovered"); }
  } else {
    batV = batmA = 0;
    batPct = -1;
    chgState = "none";
    batLow = false;
  }
}

static void evaluate(uint32_t now) {
  vehNowA = approaching(uA, now);
  vehNowB = approaching(uB, now);
  camelNow = camelOn;
  bool hazard = camelNow || alertOn;

  hazardText[0] = '\0';
  if (camelNow) strlcat(hazardText, "camel+", sizeof(hazardText));
  if (alertOn) strlcat(hazardText, "manual+", sizeof(hazardText));
  size_t hl = strlen(hazardText);
  if (hl) hazardText[hl - 1] = '\0';

  bool warmup = (now - bootMs) < WARMUP_MS;
  RoadState prev = state;
  if (warmup && !hazard) state = ST_WARMUP;
  else if (hazard) state = (vehNowA || vehNowB) ? ST_DANGER : ST_WARNING;
  else state = ST_CLEAR;

  if ((state == ST_WARNING || state == ST_DANGER) && !(prev == ST_WARNING || prev == ST_DANGER)) {
    hazardStartMs = now;
    Serial.printf("[HAZARD] %s: %s\n", STATE_NAMES[state], hazardText);
  }
  if (state == ST_DANGER && prev != ST_DANGER) Serial.println("[HAZARD] DANGER - vehicle approaching the camel");
  if (prev != state && state == ST_CLEAR && (prev == ST_WARNING || prev == ST_DANGER))
    Serial.println("[HAZARD] Road clear again");
}

// =====================================================================
//  74HC595: yellow lamp and signs. Only shifts when the pattern changes.
// =====================================================================
static int32_t ledsShown = -1;   // forces the first write

// One 74HC595: 8 outputs, bit i = Q<i>.
// Slow edges (1 µs) keep long breadboard wires from double-clocking the chip.
static void ledsWrite(uint8_t bits, bool force = false) {
  if (bits == ledsShown && !force) return;
  ledsShown = bits;
  if (!LEDS_ACTIVE_HIGH) bits = ~bits;
  digitalWrite(PIN_SR_LATCH, LOW);
  for (int8_t b = 7; b >= 0; b--) {
    digitalWrite(PIN_SR_DATA, (bits >> b) & 1u);
    delayMicroseconds(1);
    digitalWrite(PIN_SR_CLOCK, HIGH);
    delayMicroseconds(1);
    digitalWrite(PIN_SR_CLOCK, LOW);
  }
  delayMicroseconds(1);
  digitalWrite(PIN_SR_LATCH, HIGH);              // all outputs change at once
  delayMicroseconds(1);
  digitalWrite(PIN_SR_LATCH, LOW);
}

// What the outputs are doing, in words, for the dashboard's live road view
static char outSig[12] = "off", outSignC[6] = "off", outSignW[6] = "off", outSignA[6] = "off", outSignB[6] = "off", outBuzz[6] = "off";
static volatile int8_t outLed = -1;  // "leds" test: the one output that is on (bit 0-4)

static void updateOutputs(uint32_t now) {
  bool hazard = state == ST_WARNING || state == ST_DANGER;
  bool danger = state == ST_DANGER;

  uint8_t test = testOut;
  if (test != T_NONE && (hazard || (int32_t)(now - testUntilMs) >= 0)) {
    testOut = test = T_NONE;
    Serial.println(hazard ? "[TEST]  cancelled - a real hazard has priority" : "[TEST]  finished");
  }

  bool slow = (now % 1000) < 500, fast = (now % 250) < 125;
  uint8_t bits = 0;
  // --- yellow caution lamp: always flashing (1 Hz), camel or not. It is one output: both directions are in parallel.
  //     Output test "signals" holds it on steadily.
  const char *sig = "flash";
  if (test == T_SIGNALS) { bits |= 1u << Q_LAMP; sig = "on"; }
  else if (slow) bits |= 1u << Q_LAMP;

  // --- roadside signs (both directions in parallel). While a camel is crossing (or during a manual alert) the
  //     300 / 200 / 100 m warnings and the camel-crossing sign flash (1 Hz); in DANGER (a car is coming too) 4 Hz.
  //     The vehicle sensors per side still count cars and raise DANGER; they no longer pick a side's signs.
  const char *sc = "off", *sw = "off";
  if (test == T_SIGNS) { if (slow) bits |= SIGNS_MASK; sc = sw = "flash"; }
  else if (hazard) {
    if (danger ? fast : slow) bits |= SIGNS_MASK;
    sc = sw = danger ? "fast" : "flash";
  }
  const char *sA = sw, *sB = sw;
  // "Test Each LED": one output at a time, 1 s each — Q0..Q4. The dashboard names the output that should be
  // lit, so the wiring can be checked LED by LED.
  if (test == T_LEDS) {
    uint8_t b = ((now - testStartMs) / 1000) % LED_COUNT;
    bits = (uint8_t)(1u << b);
    outLed = (int8_t)b;
    sig = "off";
    sc = sw = sA = sB = "off";
  } else {
    outLed = -1;
  }

  // Rewrite the pattern every 500 ms even when it did not change: a glitch on the breadboard
  // wiring then fixes itself instead of leaving random LEDs on.
  static uint32_t lastRefreshMs = 0;
  bool refresh = now - lastRefreshMs >= 500;
  if (refresh) lastRefreshMs = now;
  ledsWrite(bits, refresh);

  // --- buzzer at the crossing: warns the camel herder that a vehicle is coming
  bool buzz = false;
  const char *bz = "off";
  if (danger) { buzz = fast; bz = "fast"; }
  if (!S.buzzer) bz = "muted";
  bool testBeep = test == T_BUZZER && slow;          // a test sounds even when the buzzer is muted
  if (test == T_BUZZER) bz = "test";
  writeOut(PIN_BUZZER, (buzz && S.buzzer) || testBeep, BUZZER_ACTIVE_HIGH);

  strlcpy(outSig, sig, sizeof(outSig));
  strlcpy(outSignC, sc, sizeof(outSignC));
  strlcpy(outSignW, sw, sizeof(outSignW));
  strlcpy(outSignA, sA, sizeof(outSignA));
  strlcpy(outSignB, sB, sizeof(outSignB));
  strlcpy(outBuzz, bz, sizeof(outBuzz));

  digitalWrite(PIN_STATUS_LED, wifiOnline ? HIGH : ((now % 1000) < 100));
}

static void doReset(const char *src) {
  if (testOut != T_NONE) { testOut = T_NONE; Serial.printf("[TEST]  stopped by %s\n", src); }
  bool was = alertOn;
  alertOn = false;
  camelOn = false;                   // re-detect from scratch: a camel still there comes back at once
  gA.blockedN = gB.blockedN = 0;
  gA.seen = gB.seen = false;
  uA.lastHitMs = uB.lastHitMs = 0;
  Serial.printf("[RESET] by %s%s\n", src, was ? " (manual alert stopped)" : "");
}

static void updateSnapshot(uint32_t now) {
  char faults[80] = "";
  char f[8];
  // US = vehicle HC-SR04 gives no echo pulse at all (sensor, 5 V or ECHO divider), CAR = it has seen a "vehicle"
  // for minutes (something parked in front of it, or carCm larger than the empty-lane distance).
  for (CarSensor *c : cars) {
    if (c->deadN >= US_FAULT_READINGS) { snprintf(f, sizeof(f), "US-%c ", c->tag); strlcat(faults, f, sizeof(faults)); }
    if (c->carSinceMs && now - c->carSinceMs > GATE_STUCK_MS) { snprintf(f, sizeof(f), "CAR-%c ", c->tag); strlcat(faults, f, sizeof(faults)); }
  }
  // OBJ = a camel IR has been blocked for minutes (something left in the gate, or the module needs adjusting).
  for (GateIr *g : gates)
    if (g->blockedSinceMs && now - g->blockedSinceMs > GATE_STUCK_MS) { snprintf(f, sizeof(f), "OBJ-%c ", g->tag); strlcat(faults, f, sizeof(faults)); }
  // Power: a module that answered once and now stays silent is a fault; a module that was never there is just "not fitted".
  if (batLow) strlcat(faults, "BAT-LOW ", sizeof(faults));
  if (inaSolar.everSeen && !inaSolar.present) strlcat(faults, "INA-S ", sizeof(faults));
  if (inaBat.everSeen && !inaBat.present) strlcat(faults, "INA-B ", sizeof(faults));

  portENTER_CRITICAL(&snapMux);
  snap.state = state;
  strlcpy(snap.hazard, hazardText, sizeof(snap.hazard));
  snap.hazardSec = (state == ST_WARNING || state == ST_DANGER) ? (now - hazardStartMs) / 1000 : 0;
  snap.camel = camelOn;
  strlcpy(snap.camelDir, camelDir, sizeof(snap.camelDir));
  snap.irA = gA.raw; snap.irB = gB.raw;
  snap.distA = uA.dist; snap.distB = uB.dist;
  snap.carA = uA.car; snap.carB = uB.car;
  snap.vehA = vehNowA; snap.vehB = vehNowB;
  snap.vehCount = vehCount; snap.vehCountA = uA.count; snap.vehCountB = uB.count;
  snap.camelCount = camelCount;
  strlcpy(snap.lastDir, lastDir, sizeof(snap.lastDir));
  snap.pvOk = inaSolar.present && inaSolar.valid;
  snap.batOk = inaBat.present && inaBat.valid;
  snap.sun = sunNow;
  snap.pvV = pvV; snap.pvmA = pvmA; snap.batV = batV; snap.batmA = batmA;
  snap.batPct = batPct;
  strlcpy(snap.chg, chgState, sizeof(snap.chg));
  strlcpy(snap.faults, faults, sizeof(snap.faults));
  strlcpy(snap.sig, outSig, sizeof(snap.sig));
  strlcpy(snap.signC, outSignC, sizeof(snap.signC));
  strlcpy(snap.signW, outSignW, sizeof(snap.signW));
  strlcpy(snap.signA, outSignA, sizeof(snap.signA));
  strlcpy(snap.signB, outSignB, sizeof(snap.signB));
  strlcpy(snap.buzz, outBuzz, sizeof(snap.buzz));
  portEXIT_CRITICAL(&snapMux);
}

// =====================================================================
//  Tiny JSON reader — enough for the small, well-known command objects the
//  dashboard sends. Avoids pulling in an extra library.
// =====================================================================
static int jsonAfterKey(const String &s, const char *key, int from = 0) {
  String needle = String("\"") + key + "\":";
  int i = s.indexOf(needle, from);
  return i < 0 ? -1 : i + needle.length();
}

static float jsonNum(const String &s, const char *key, float def) {
  int i = jsonAfterKey(s, key);
  if (i < 0) return def;
  return s.substring(i, min((int)s.length(), i + 24)).toFloat();
}

// Integers must not go through float: command ids are large and would lose precision.
static long jsonLong(const String &s, const char *key, long def) {
  int i = jsonAfterKey(s, key);
  if (i < 0) return def;
  while (i < (int)s.length() && s[i] == ' ') i++;
  return s.substring(i, min((int)s.length(), i + 12)).toInt();
}

static bool jsonBool(const String &s, const char *key, bool def) {
  int i = jsonAfterKey(s, key);
  if (i < 0) return def;
  while (i < (int)s.length() && s[i] == ' ') i++;
  return s.substring(i, i + 4) == "true";
}

static bool jsonHas(const String &s, const char *key) { return jsonAfterKey(s, key) >= 0; }

static bool jsonStr(const String &s, const char *key, char *out, size_t n) {
  int i = jsonAfterKey(s, key);
  if (i < 0) return false;
  while (i < (int)s.length() && s[i] != '"') {
    if (s[i] == ',' || s[i] == '}') return false;  // not a string value
    i++;
  }
  if (i >= (int)s.length()) return false;
  String v;
  for (int j = i + 1; j < (int)s.length() && s[j] != '"'; j++) {
    char c = s[j];
    if (c == '\\' && j + 1 < (int)s.length()) c = s[++j];
    v += c;
  }
  strlcpy(out, v.c_str(), n);
  return true;
}

// Escape a string for a JSON value. SSIDs can contain any byte; an unescaped quote
// would make every telemetry POST invalid and cut the device off from the dashboard.
static const char *jsonEsc(const char *in, char *out, size_t n) {
  size_t k = 0;
  for (; *in && k + 2 < n; in++) {
    unsigned char c = (unsigned char)*in;
    if (c == '"' || c == '\\') { out[k++] = '\\'; out[k++] = c; }
    else if (c >= 0x20) out[k++] = c;   // drop control characters, keep UTF-8 bytes
  }
  out[k] = '\0';
  return out;
}

// Device names are shown everywhere and sent in every POST: keep them to safe characters.
static void sanitizeName(char *s) {
  char *w = s;
  for (char *r = s; *r; r++)
    if (isalnum((unsigned char)*r) || *r == ' ' || *r == '-' || *r == '_' || *r == '.') *w++ = *r;
  *w = '\0';
  if (!s[0]) strcpy(s, DEVICE_ID);
}

static String htmlEsc(const String &in) {
  String o;
  for (size_t i = 0; i < in.length(); i++) {
    char c = in[i];
    if (c == '<') o += "&lt;"; else if (c == '>') o += "&gt;"; else if (c == '&') o += "&amp;";
    else if (c == '"') o += "&quot;"; else o += c;
  }
  return o;
}

// =====================================================================
//  Network: connection, setup access point, telemetry, commands
// =====================================================================
static WebServer portal(80);
static bool apActive = false;
static uint8_t failedRounds = 0;
static long ackId = 0;                  // command id echoed back in the next telemetry
static long lastSentAck = 0;            // ack value that actually left in a successful POST
// Restarting the device is deferred until the server has SEEN the ack, otherwise it keeps
// resending the command to the freshly booted device and we restart in a loop.
static long rebootAfterAck = 0, factoryAfterAck = 0;
static bool pendingReconnect = false;
static bool dashboardOk = false;
static char netUrl[96] = "";

static void serialPoll();   // USB configuration (below); also polled while connecting

// Port 443 = a dashboard in the cloud (Render / Railway): HTTPS. Anything else = the PC dashboard on the LAN: HTTP.
static void buildUrl() {
  if (!S.host[0]) netUrl[0] = '\0';
  else if (S.port == 443) snprintf(netUrl, sizeof(netUrl), "https://%s/api/telemetry", S.host);
  else snprintf(netUrl, sizeof(netUrl), "http://%s:%u/api/telemetry", S.host, S.port);
}

// ---- setup access point -------------------------------------------------
static const char PORTAL_PAGE[] PROGMEM = R"HTML(<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Camel Guard setup</title>
<style>body{font-family:system-ui,sans-serif;background:#0b1a30;color:#e7eefb;margin:0;padding:22px}
.c{max-width:420px;margin:auto;background:#101a2c;border:1px solid #1f2d45;border-radius:14px;padding:20px}
h1{font-size:19px;margin:0 0 4px}p{color:#9fb0cb;font-size:13px;line-height:1.5}
label{display:block;font-size:12px;font-weight:600;margin:14px 0 6px;color:#9fb0cb}
input,select{width:100%;box-sizing:border-box;padding:10px;border-radius:9px;border:1px solid #1f2d45;
background:#0b1a30;color:#e7eefb;font-size:14px}
button{width:100%;margin-top:18px;padding:12px;border:0;border-radius:9px;background:#2563eb;color:#fff;
font-size:15px;font-weight:600}</style>
<div class=c><h1>Camel Guard &mdash; WiFi setup</h1>
<p>The road warnings keep working while you do this. Pick the network the dashboard PC is on.</p>
<form method=POST action=/save>
<label>Network</label><select name=ssid id=s>%SCAN%</select>
<label>Password</label><input name=pass type=password placeholder="WiFi password">
<label>Dashboard address (optional)</label><input name=host placeholder="leave empty to find it automatically">
<button type=submit>Save &amp; connect</button></form></div>)HTML";

static void portalRoot() {
  int n = WiFi.scanNetworks(false, false);
  String opts;
  for (int i = 0; i < n && i < 20; i++) {
    String ss = WiFi.SSID(i);
    if (!ss.length()) continue;
    String e = htmlEsc(ss);   // a nearby network name must not be able to inject HTML
    opts += "<option value=\"" + e + "\">" + e + "  (" + String(WiFi.RSSI(i)) + " dBm)</option>";
  }
  if (!opts.length()) opts = "<option value=\"\">no network found — reload</option>";
  String page = FPSTR(PORTAL_PAGE);
  page.replace("%SCAN%", opts);
  portal.send(200, "text/html; charset=utf-8", page);
}

static void portalSave() {
  String ssid = portal.arg("ssid"), pass = portal.arg("pass"), host = portal.arg("host");
  ssid.trim(); host.trim();
  if (!ssid.length()) { portal.send(400, "text/plain", "No network selected."); return; }

  // Put the new network first, keep the others (without duplicating it).
  Settings old = S;
  strlcpy(S.ssid[0], ssid.c_str(), sizeof(S.ssid[0]));
  strlcpy(S.pass[0], pass.c_str(), sizeof(S.pass[0]));
  uint8_t k = 1;
  for (uint8_t i = 0; i < old.nNets && k < MAX_NETWORKS; i++) {
    if (!strcmp(old.ssid[i], S.ssid[0])) continue;
    strlcpy(S.ssid[k], old.ssid[i], sizeof(S.ssid[k]));
    strlcpy(S.pass[k], old.pass[i], sizeof(S.pass[k]));
    k++;
  }
  S.nNets = k;
  if (host.length()) { strlcpy(S.host, host.c_str(), sizeof(S.host)); }
  saveSettings();
  buildUrl();
  portal.send(200, "text/html; charset=utf-8",
              "<meta charset=utf-8><body style='font-family:system-ui;background:#0b1a30;color:#e7eefb;padding:26px'>"
              "<h2>Saved.</h2><p>The device is connecting to <b>" + htmlEsc(ssid) +
              "</b>. This setup network disappears once it succeeds.</p>");
  Serial.printf("[AP]    New network saved from the setup page: \"%s\"\n", S.ssid[0]);
  failedRounds = 0;
  pendingReconnect = true;
}

static void startAP() {
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP(apName, AP_PASS);
  portal.on("/", portalRoot);
  portal.on("/save", HTTP_POST, portalSave);
  portal.onNotFound(portalRoot);
  portal.begin();
  apActive = true;
  Serial.printf("[AP]    No saved network answered. Setup AP \"%s\" is up — connect and open http://%s\n",
                apName, WiFi.softAPIP().toString().c_str());
}

static void stopAP() {
  if (!apActive) return;
  portal.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
  apActive = false;
  Serial.println("[AP]    Setup access point closed (station connected)");
}

// ---- connecting ---------------------------------------------------------
static bool tryConnectOnce(const char *ssid, const char *pass) {
  Serial.printf("[WiFi] Trying \"%s\" ...\n", ssid);
  WiFi.begin(ssid, pass);
  uint32_t t0 = millis();
  while (millis() - t0 < STA_TRY_MS) {
    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf("[WiFi] Connected to \"%s\"  IP=%s  gateway=%s  RSSI=%d dBm\n", ssid,
                    WiFi.localIP().toString().c_str(), WiFi.gatewayIP().toString().c_str(), WiFi.RSSI());
      return true;
    }
    if (apActive) portal.handleClient();
    serialPoll();
    if (pendingReconnect) break;   // a new WiFi list arrived over USB: start over with it
    delay(50);
  }
  WiFi.disconnect();
  return false;
}

static void wifiRound() {
  if (S.nNets == 0) {
    if (!apActive) startAP();
    for (int i = 0; i < 10 && !pendingReconnect; i++) { serialPoll(); delay(50); }
    return;
  }
  for (uint8_t i = 0; i < S.nNets; i++) {
    if (!S.ssid[i][0]) continue;
    if (tryConnectOnce(S.ssid[i], S.pass[i])) {
      failedRounds = 0;
      stopAP();
      return;
    }
    if (pendingReconnect) return;  // not a failed round — the list was just replaced
  }
  failedRounds++;
  Serial.printf("[WiFi] None of the %u saved network(s) answered (round %u). "
                "ESP32 needs 2.4 GHz WPA/WPA2-Personal.\n", S.nNets, failedRounds);
  if (failedRounds >= AP_AFTER_ROUNDS && !apActive) startAP();
}

// ---- finding the dashboard by UDP broadcast -----------------------------
static bool discoverServer() {
  WiFiUDP udp;
  if (!udp.begin(0)) return false;
  const char *msg = "CAMELGUARD_DISCOVER";
  IPAddress bc((uint32_t)WiFi.localIP() | ~(uint32_t)WiFi.subnetMask());
  for (int i = 0; i < 2; i++) {
    IPAddress dst = i ? IPAddress(255, 255, 255, 255) : bc;
    udp.beginPacket(dst, DISCOVERY_PORT);
    udp.write((const uint8_t *)msg, strlen(msg));
    udp.endPacket();
  }
  uint32_t t0 = millis();
  while (millis() - t0 < 900) {
    int n = udp.parsePacket();
    if (n > 0) {
      char buf[64] = {0};
      int len = udp.read(buf, sizeof(buf) - 1);
      if (len > 0 && strncmp(buf, "CAMELGUARD_HERE:", 16) == 0) {
        char *ip = buf + 16;
        char *colon = strrchr(ip, ':');
        uint16_t port = SERVER_PORT;
        if (colon) { *colon = '\0'; port = (uint16_t)atoi(colon + 1); }
        if (strcmp(S.host, ip) != 0 || S.port != port) {
          strlcpy(S.host, ip, sizeof(S.host));
          S.port = port;
          saveSettings();
        }
        buildUrl();
        udp.stop();
        Serial.printf("[NET]  Dashboard found by broadcast: %s:%u\n", S.host, S.port);
        return true;
      }
    }
    delay(20);
  }
  udp.stop();
  return false;
}

// ---- applying a command from the dashboard ------------------------------
static void applyConfig(const String &r) {
  if (jsonHas(r, "name")) { jsonStr(r, "name", S.name, sizeof(S.name)); sanitizeName(S.name); }
  if (jsonHas(r, "carCm")) S.carCm = (uint8_t)clampf(jsonNum(r, "carCm", S.carCm), LIMIT_CAR_MIN, LIMIT_CAR_MAX);
  if (jsonHas(r, "confirm")) S.confirm = (uint8_t)clampf(jsonNum(r, "confirm", S.confirm), 1, 20);
  if (jsonHas(r, "holdSec")) S.holdSec = (uint16_t)clampf(jsonNum(r, "holdSec", S.holdSec), LIMIT_HOLD_MIN, LIMIT_HOLD_MAX);
  if (jsonHas(r, "approachSec")) S.approachSec = (uint8_t)clampf(jsonNum(r, "approachSec", S.approachSec), 1, 20);
  if (jsonHas(r, "useA")) S.useA = jsonBool(r, "useA", S.useA);
  if (jsonHas(r, "useB")) S.useB = jsonBool(r, "useB", S.useB);
  if (jsonHas(r, "buzzer")) S.buzzer = jsonBool(r, "buzzer", S.buzzer);
  saveSettings();
  Serial.printf("[CONFIG] Updated: vehicle<=%u cm IR confirm=%u x 50 ms hold=%u s approach=%u s IR A=%d B=%d buzzer=%d\n",
                S.carCm, S.confirm, S.holdSec, S.approachSec, (int)S.useA, (int)S.useB, (int)S.buzzer);
}

// Index of the first `target` at or after `from` that is not inside a JSON string, so that a
// password such as "ab}c]d" does not end the object or the array early.
static int jsonFindOutside(const String &s, int from, char target) {
  bool inStr = false;
  for (int i = from; i >= 0 && i < (int)s.length(); i++) {
    char c = s[i];
    if (inStr) { if (c == '\\') i++; else if (c == '"') inStr = false; }
    else if (c == '"') inStr = true;
    else if (c == target) return i;
  }
  return -1;
}

static bool applyWifi(const String &r) {
  int arr = jsonAfterKey(r, "nets");            // tolerant of "nets": [ with spaces
  while (arr >= 0 && arr < (int)r.length() && r[arr] == ' ') arr++;
  if (arr < 0 || arr >= (int)r.length() || r[arr] != '[') return false;
  int end = jsonFindOutside(r, arr + 1, ']');
  if (end < 0) return false;
  Settings old = S;
  uint8_t cnt = 0;
  int p = arr + 1;
  while (cnt < MAX_NETWORKS) {
    int o = jsonFindOutside(r, p, '{');
    if (o < 0 || o > end) break;
    int c = jsonFindOutside(r, o + 1, '}');
    if (c < 0 || c > end) break;
    String obj = r.substring(o, c + 1);
    char ss[33] = "", pw[64] = "";
    if (jsonStr(obj, "ssid", ss, sizeof(ss)) && ss[0]) {
      jsonStr(obj, "pass", pw, sizeof(pw));
      if (!pw[0] || jsonBool(obj, "keep", false)) {   // keep the password already stored for this SSID
        for (uint8_t k = 0; k < old.nNets; k++)
          if (strcmp(old.ssid[k], ss) == 0) { strlcpy(pw, old.pass[k], sizeof(pw)); break; }
      }
      strlcpy(S.ssid[cnt], ss, sizeof(S.ssid[cnt]));
      strlcpy(S.pass[cnt], pw, sizeof(S.pass[cnt]));
      cnt++;
    }
    p = c + 1;
  }
  if (cnt == 0) return false;
  for (uint8_t i = cnt; i < MAX_NETWORKS; i++) { S.ssid[i][0] = '\0'; S.pass[i][0] = '\0'; }
  S.nNets = cnt;

  char host[41] = "";
  if (jsonStr(r, "host", host, sizeof(host))) {
    strlcpy(S.host, host, sizeof(S.host));
    S.port = (uint16_t)jsonNum(r, "port", S.port);
  }
  saveSettings();
  buildUrl();
  Serial.printf("[WiFi] New list saved (%u networks). First: \"%s\"\n", S.nNets, S.ssid[0]);

  // Only reconnect when the network we are on is no longer in the list.
  bool stillListed = false;
  for (uint8_t i = 0; i < S.nNets; i++)
    if (WiFi.SSID() == String(S.ssid[i])) stillListed = true;
  if (!stillListed) pendingReconnect = true;
  return true;
}

static void handleCommand(const String &r) {
  char cmd[16] = "";
  if (!jsonStr(r, "cmd", cmd, sizeof(cmd)) || !strcmp(cmd, "none")) return;
  long id = jsonLong(r, "id", 0);
  if (id && id == ackId) return;  // already handled, the server just has not seen the ack yet

  if (!strcmp(cmd, "reset"))          resetRequested = true;
  else if (!strcmp(cmd, "config"))    applyConfig(r);
  else if (!strcmp(cmd, "wifi"))      applyWifi(r);
  else if (!strcmp(cmd, "reboot"))    rebootAfterAck = id;    // done once the ack is delivered
  else if (!strcmp(cmd, "factory"))   factoryAfterAck = id;
  else if (!strcmp(cmd, "alert")) {
    alertOn = jsonBool(r, "on", true);
    Serial.printf("[ALERT] Manual alert %s from the dashboard\n", alertOn ? "STARTED" : "stopped");
  }
  else if (!strcmp(cmd, "test")) {
    char out[10] = "none";
    jsonStr(r, "out", out, sizeof(out));
    long sec = jsonLong(r, "sec", 5);
    sec = sec < 1 ? 1 : (sec > TEST_MAX_SEC ? TEST_MAX_SEC : sec);
    uint8_t t = T_NONE;
    for (uint8_t i = 1; i < sizeof(TEST_NAMES) / sizeof(TEST_NAMES[0]); i++)
      if (!strcmp(out, TEST_NAMES[i])) t = i;
    if (t != T_NONE && (state == ST_WARNING || state == ST_DANGER)) {
      Serial.printf("[TEST]  %s refused - hazard active\n", out);
    } else if (t != T_NONE) {
      testStartMs = millis();
      testUntilMs = testStartMs + (uint32_t)sec * 1000;   // before testOut: the output loop reads both
      testOut = t;
      Serial.printf("[TEST]  %s for %ld s (from the dashboard)\n", TEST_NAMES[t], sec);
    } else if (testOut != T_NONE) {
      testOut = T_NONE;
      Serial.println("[TEST]  stopped from the dashboard");
    }
  }
  else { Serial.printf("[NET]  Unknown command \"%s\" ignored\n", cmd); return; }

  ackId = id;
  Serial.printf("[NET]  Command \"%s\" (id %ld) accepted\n", cmd, id);
}

// ---- configuration over USB ---------------------------------------------
// The dashboard (Settings -> Network -> "Configure over USB", using Web Serial) sends
// lines that start with '@'. Each reply is ONE line starting with "@@" so it can be picked
// out of the normal log output. The same commands can be typed in the Serial Monitor.
//   @info                      -> current connection, saved networks (never passwords)
//   @scan                      -> nearby WiFi networks
//   @wifi {"nets":[...]}       -> same payload as the dashboard "wifi" command
static void usbReply(const String &json) {
  String o = "@@" + json + "\n";
  Serial.print(o);   // a single write, so a log line from the other core cannot split it
}

static String usbInfo() {
  char e1[52], e2[70], e3[84], e4[70];
  bool on = WiFi.status() == WL_CONNECTED;
  String j = "{\"t\":\"info\",\"fw\":\"" FW_VERSION "\",\"name\":\"";
  j += jsonEsc(S.name, e1, sizeof(e1));
  j += String("\",\"mac\":\"") + macText + "\",\"wifi\":" + (on ? "true" : "false");
  j += ",\"ssid\":\"";
  j += on ? jsonEsc(WiFi.SSID().c_str(), e2, sizeof(e2)) : "";
  j += "\",\"ip\":\"" + (on ? WiFi.localIP().toString() : String("")) + "\",\"rssi\":" + String(on ? WiFi.RSSI() : 0);
  j += String(",\"ap\":") + (apActive ? "true" : "false") + ",\"apName\":\"" + apName + "\",\"boot\":\"" + bootReason + "\""
       + ",\"dash\":" + (dashboardOk ? "true" : "false");
  j += ",\"host\":\"";
  j += jsonEsc(S.host, e3, sizeof(e3));
  j += "\",\"state\":\"" + String(STATE_NAMES[state]) + "\",\"alert\":" + (alertOn ? "true" : "false") + ",\"nets\":[";
  for (uint8_t i = 0; i < S.nNets; i++) {
    if (i) j += ",";
    j += "\"" + String(jsonEsc(S.ssid[i], e4, sizeof(e4))) + "\"";
  }
  j += "]}";
  return j;
}

static void usbScan() {
  Serial.println("[USB]  Scanning WiFi networks...");
  int n = WiFi.scanNetworks();
  if (n < 0 && WiFi.status() != WL_CONNECTED) {   // busy with a connection attempt: stop it and retry
    WiFi.disconnect();
    delay(100);
    n = WiFi.scanNetworks();
  }
  if (n < 0) { usbReply("{\"t\":\"scan\",\"error\":\"scan failed, try again\"}"); return; }
  String j = "{\"t\":\"scan\",\"nets\":[";
  int shown = 0;
  for (int i = 0; i < n && shown < 30; i++) {
    String ss = WiFi.SSID(i);
    if (!ss.length()) continue;   // hidden network
    char e[70];
    wifi_auth_mode_t a = WiFi.encryptionType(i);
    if (shown++) j += ",";
    j += "{\"ssid\":\"" + String(jsonEsc(ss.c_str(), e, sizeof(e))) + "\",\"rssi\":" + String(WiFi.RSSI(i)) +
         ",\"ch\":" + String(WiFi.channel(i)) + ",\"lock\":" + (a != WIFI_AUTH_OPEN ? "true" : "false") +
         ",\"ent\":" + (a == WIFI_AUTH_WPA2_ENTERPRISE ? "true" : "false") + "}";
  }
  WiFi.scanDelete();
  j += "]}";
  usbReply(j);
}

static void usbWifi(const String &payload) {
  if (!applyWifi(payload)) {
    usbReply("{\"t\":\"wifi\",\"ok\":false,\"error\":\"no valid network in the list\"}");
    return;
  }
  failedRounds = 0;
  // Over USB the user just picked a network: connect to the first one now, even if the
  // current network is still somewhere in the list.
  if (WiFi.status() != WL_CONNECTED || WiFi.SSID() != String(S.ssid[0])) pendingReconnect = true;
  char e[70];
  usbReply(String("{\"t\":\"wifi\",\"ok\":true,\"n\":") + String((int)S.nNets) + ",\"first\":\"" +
           jsonEsc(S.ssid[0], e, sizeof(e)) + "\"}");
}

static void serialPoll() {
  static char buf[1024];
  static size_t len = 0;
  static bool overflow = false;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c != '\n') {
      if (len < sizeof(buf) - 1) buf[len++] = c; else overflow = true;
      continue;
    }
    buf[len] = '\0';
    bool tooLong = overflow;
    String line(buf);
    len = 0;
    overflow = false;
    line.trim();
    if (!line.length() || line[0] != '@') continue;   // not for us
    if (tooLong) { usbReply("{\"t\":\"error\",\"msg\":\"line too long\"}"); continue; }
    if (line.startsWith("@info")) usbReply(usbInfo());
    else if (line.startsWith("@scan")) usbScan();
    else if (line.startsWith("@wifi ")) usbWifi(line.substring(6));
    else usbReply("{\"t\":\"error\",\"msg\":\"unknown command\"}");
  }
}

// ---- telemetry ----------------------------------------------------------
static bool postTelemetry() {
  static char body[2560];
  const long sentAck = ackId;   // handleCommand() may change ackId while we are in here
  Snapshot s;
  portENTER_CRITICAL(&snapMux);
  s = snap;
  portEXIT_CRITICAL(&snapMux);

  String ip = WiFi.localIP().toString();
  char eName[52], eSsid[70], eHost[84], eNet[70];
  jsonEsc(S.name, eName, sizeof(eName));
  jsonEsc(WiFi.SSID().c_str(), eSsid, sizeof(eSsid));
  jsonEsc(S.host, eHost, sizeof(eHost));
  uint8_t tOut = testOut;
  int32_t tMs = (int32_t)(testUntilMs - millis());
  uint32_t tLeft = (tOut != T_NONE && tMs > 0) ? (uint32_t)(tMs + 999) / 1000 : 0;
  if (!tLeft) tOut = T_NONE;
  const char *B = "false", *T = "true";
  int k = snprintf(body, sizeof(body),
      "{\"id\":\"%s\",\"mac\":\"%s\",\"fw\":\"%s\",\"ip\":\"%s\",\"ssid\":\"%s\",\"host\":\"%s:%u\",\"rssi\":%d,\"up\":%lu,"
      "\"boot\":\"%s\",\"apName\":\"%s\",\"ack\":%ld,\"alert\":%s,\"test\":\"%s\",\"testSec\":%lu,\"led\":%d,"
      "\"state\":\"%s\",\"hazard\":\"%s\",\"hazardSec\":%lu,"
      "\"camel\":%s,\"camelDir\":\"%s\",\"irA\":%s,\"irB\":%s,"
      "\"distA\":%.1f,\"distB\":%.1f,\"carA\":%s,\"carB\":%s,\"vehA\":%s,\"vehB\":%s,\"veh\":%s,"
      "\"vehCount\":%lu,\"vehCountA\":%lu,\"vehCountB\":%lu,\"lastDir\":\"%s\",\"camelCount\":%lu,"
      "\"vbat\":%.2f,"
      "\"pvOk\":%s,\"batOk\":%s,\"sun\":%s,\"pvV\":%.2f,\"pvmA\":%.0f,\"batV\":%.2f,\"batmA\":%.0f,\"batPct\":%d,\"chg\":\"%s\","
      "\"sig\":\"%s\",\"signC\":\"%s\",\"signW\":\"%s\",\"signA\":\"%s\",\"signB\":\"%s\",\"buzz\":\"%s\",\"faults\":\"%s\",",
      eName, macText, FW_VERSION, ip.c_str(), eSsid, eHost, S.port, WiFi.RSSI(), (unsigned long)(millis() / 1000),
      bootReason, apName, ackId, alertOn ? T : B, TEST_NAMES[tOut], (unsigned long)tLeft, tOut == T_LEDS ? (int)outLed : -1,
      STATE_NAMES[s.state], s.hazard, (unsigned long)s.hazardSec,
      s.camel ? T : B, s.camelDir, s.irA ? T : B, s.irB ? T : B,
      // distance: -1 = nothing in range / no echo (the faults tell the two apart)
      (s.distA > 0 && s.distA < FAR_CM) ? s.distA : -1.0f, (s.distB > 0 && s.distB < FAR_CM) ? s.distB : -1.0f,
      s.carA ? T : B, s.carB ? T : B,
      s.vehA ? T : B, s.vehB ? T : B, (s.vehA || s.vehB) ? T : B,
      (unsigned long)s.vehCount, (unsigned long)s.vehCountA, (unsigned long)s.vehCountB, s.lastDir,
      (unsigned long)s.camelCount, s.batOk ? s.batV : 0.0f,
      s.pvOk ? T : B, s.batOk ? T : B, s.sun ? T : B, s.pvV, s.pvmA, s.batV, s.batmA, (int)s.batPct, s.chg,
      s.sig, s.signC, s.signW, s.signA, s.signB, s.buzz, s.faults);

  k += snprintf(body + k, sizeof(body) - k,
      "\"cfg\":{\"name\":\"%s\",\"carCm\":%u,\"confirm\":%u,\"holdSec\":%u,\"approachSec\":%u,"
      "\"useA\":%s,\"useB\":%s,\"buzzer\":%s},\"nets\":[",
      eName, S.carCm, S.confirm, S.holdSec, S.approachSec, S.useA ? T : B,
      S.useB ? T : B, S.buzzer ? T : B);
  for (uint8_t i = 0; i < S.nNets && k < (int)sizeof(body) - 80; i++)
    k += snprintf(body + k, sizeof(body) - k, "%s\"%s\"", i ? "," : "", jsonEsc(S.ssid[i], eNet, sizeof(eNet)));
  snprintf(body + k, sizeof(body) - k, "]}");

  HTTPClient http;
  // A cloud dashboard (HTTPS) needs a TLS handshake on every POST and sits on the internet: allow longer.
  // The certificate is not checked (setInsecure) - acceptable for this school model, see docs/DEPLOY_CLOUD.id.md.
  static WiFiClientSecure tls;
  bool https = strncmp(netUrl, "https://", 8) == 0;
  http.setConnectTimeout(https ? 6000 : 1500);
  http.setTimeout(https ? 6000 : 1500);
  if (https) {
    tls.setInsecure();
    if (!http.begin(tls, netUrl)) return false;
  } else if (!http.begin(netUrl)) return false;
  http.addHeader("Content-Type", "application/json");
  if (DEVICE_KEY[0]) http.addHeader("X-Device-Key", DEVICE_KEY);   // must match DEVICE_KEY on the cloud dashboard
  int code = http.POST((uint8_t *)body, strlen(body));
  bool ok = false;
  if (code == 200) {
    String resp = http.getString();
    lastSentAck = sentAck;   // the server has now seen this ack
    handleCommand(resp);
    ok = true;
  }
  http.end();
  return ok;
}

// =====================================================================
//  Network task (core 0) — never blocks sensing or the warnings on core 1
// =====================================================================
static void netTask(void *) {
  uint32_t lastPost = 0, lastNetLog = 0;
  uint8_t postFails = 0;

  for (;;) {
    if (apActive) portal.handleClient();
    serialPoll();

    if (pendingReconnect) {
      pendingReconnect = false;
      Serial.println("[WiFi] Reconnecting with the new settings");
      WiFi.disconnect();
      delay(200);
    }

    wifiOnline = WiFi.status() == WL_CONNECTED;

    if (!wifiOnline) {
      dashboardOk = false;
      wifiRound();
      vTaskDelay(pdMS_TO_TICKS(apActive ? 10 : 500));
      continue;
    }

    uint32_t now = millis();
    if (now - lastPost >= TELEMETRY_INTERVAL_MS) {
      lastPost = now;

      if (!netUrl[0] && !discoverServer()) {
        if (now - lastNetLog > 5000) {
          lastNetLog = now;
          Serial.println("[NET]  Dashboard address unknown and no answer to the UDP broadcast. "
                         "Is dashboard/server.py running on this network?");
        }
      } else if (netUrl[0]) {
        if (postTelemetry()) {
          postFails = 0;
          if (!dashboardOk) Serial.printf("[NET]  Dashboard connected OK (%s)\n", netUrl);
          dashboardOk = true;
          // Restart only once the POST that carried the ack has succeeded, so the
          // server has dropped the command and will not resend it after the reboot.
          if (factoryAfterAck && lastSentAck == factoryAfterAck) {
            Serial.println("[CONFIG] Factory reset — clearing stored settings");
            prefs.clear();
            delay(200);
            ESP.restart();
          }
          if (rebootAfterAck && lastSentAck == rebootAfterAck) {
            Serial.println("[CONFIG] Reboot requested from the dashboard");
            delay(200);
            ESP.restart();
          }
        } else {
          dashboardOk = false;
          if (++postFails >= 5) {   // the PC may have a new IP — ask for it
            postFails = 0;
            if (!discoverServer() && now - lastNetLog > 5000) {
              lastNetLog = now;
              Serial.printf("[NET]  POST to %s failed. Check: dashboard running? Windows Firewall allows TCP %u?\n",
                            netUrl, S.port);
            }
          }
        }
      }
    }
    vTaskDelay(pdMS_TO_TICKS(apActive ? 10 : 25));
  }
}

// =====================================================================
void setup() {
  Serial.setRxBufferSize(2048);   // room for a whole "@wifi" line from the dashboard (USB setup)
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== Camel Guard ESP32 v" FW_VERSION " ===");
  readBootReason();
  Serial.printf("[BOOT] Reset reason: %s\n", bootReason);

  readMac();          // before loadSettings(): a fresh board's default name uses it
  loadSettings();
  sanitizeName(S.name);
  buildUrl();

  pinMode(PIN_BUZZER, OUTPUT);
  writeOut(PIN_BUZZER, false, BUZZER_ACTIVE_HIGH);
  pinMode(PIN_STATUS_LED, OUTPUT);
  digitalWrite(PIN_SR_LATCH, LOW);
  pinMode(PIN_SR_DATA, OUTPUT);
  pinMode(PIN_SR_CLOCK, OUTPUT);
  pinMode(PIN_SR_LATCH, OUTPUT);
  ledsWrite(0, true);   // the 74HC595 powers up with random outputs

  // Vehicle HC-SR04: TRIG out, ECHO in (timed by an interrupt). The pull-down keeps an unplugged ECHO from
  // floating and flooding the CPU with interrupts (it then reads "no echo" = fault US-A / US-B).
  for (CarSensor *c : cars) {
    pinMode(c->trig, OUTPUT);
    digitalWrite(c->trig, LOW);
    pinMode(c->echo, INPUT_PULLDOWN);
    attachInterruptArg(digitalPinToInterrupt(c->echo), echoIsr, c, CHANGE);
  }
  // Camel IR modules drive their output themselves; the pull-up only keeps an unplugged input from
  // floating (an unplugged IR then reads "clear").
  pinMode(PIN_CAMEL_IR_A, CAMEL_IR_ACTIVE_LOW ? INPUT_PULLUP : INPUT_PULLDOWN);
  pinMode(PIN_CAMEL_IR_B, CAMEL_IR_ACTIVE_LOW ? INPUT_PULLUP : INPUT_PULLDOWN);

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 100000);   // the two INA219 (solar panel, battery); skipped when not fitted
  Wire.setTimeOut(20);

  // Short self-test: every LED in order, Q0 -> Q4 (yellow lamp, 300 m, 200 m, 100 m, camel sign)
  // shows the wiring is right, then a short beep.
  for (uint8_t i = 0; i < LED_COUNT; i++) { ledsWrite(1u << i); delay(100); }
  ledsWrite(0);
  if (S.buzzer) { writeOut(PIN_BUZZER, true, BUZZER_ACTIVE_HIGH); delay(80); writeOut(PIN_BUZZER, false, BUZZER_ACTIVE_HIGH); }

  WiFi.mode(WIFI_STA);
  // No modem sleep: with power save on, a phone hotspot saw up to ~240 ms replies and lost
  // packets (measured on the fire-alarm project, 2026-09-21). Costs ~80 mA — fine on USB power.
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);

  memset(&snap, 0, sizeof(snap));
  bootMs = millis();
  xTaskCreatePinnedToCore(netTask, "net", 12288, nullptr, 1, nullptr, 0);

  Serial.printf("Device \"%s\" · %u saved WiFi network(s)\n", S.name, S.nNets);
  for (uint8_t i = 0; i < S.nNets; i++) Serial.printf("  %u. \"%s\"\n", i + 1, S.ssid[i]);
  Serial.printf("Dashboard: %s\n", netUrl[0] ? netUrl : "(auto-discover by UDP broadcast)");
  Serial.printf("If nothing connects, join WiFi \"%s\" (password \"%s\") and open http://192.168.4.1\n", apName, AP_PASS);
  Serial.println("Or connect over USB: dashboard Settings -> Network -> Configure over USB (type @info here to test)");
  Serial.printf("Camel gate: IR A %s (D%u), IR B %s (D%u) · IR confirm %u x 50 ms · hold %u s\n",
                S.useA ? "on" : "off", PIN_CAMEL_IR_A, S.useB ? "on" : "off", PIN_CAMEL_IR_B, S.confirm, S.holdSec);
  Serial.printf("Vehicle sensors (HC-SR04, 400 m points): A D%u/D%u, B D%u/D%u · car when <= %u cm across the lane\n",
                PIN_CAR_A_TRIG, PIN_CAR_A_ECHO, PIN_CAR_B_TRIG, PIN_CAR_B_ECHO, S.carCm);
}

void loop() {
  uint32_t now = millis();
  static uint32_t lastSample = 0, lastGate = 0, lastEval = 0, lastLog = 0, lastPower = 0;
  static bool turnB = false;

  // Each vehicle HC-SR04 every 50 ms, A and B taking turns 25 ms apart so they never hear each other;
  // the camel gate (2 IR) every 50 ms.
  if (now - lastSample >= 25) {
    lastSample = now;
    sampleCar(turnB ? uB : uA, now);
    turnB = !turnB;
  }
  if (now - lastGate >= 50) {
    lastGate = now;
    sampleGate(now);
  }
  if (now - lastPower >= 250) {
    lastPower = now;
    powerStep(now);
  }
  if (now - lastEval >= 100) {
    lastEval = now;
    evaluate(now);
    updateSnapshot(now);
  }
  if (now - lastLog >= 1000) {
    lastLog = now;
    Serial.printf("%-7s camel=%d from %s | IR A=%d B=%d | car A %.1f cm B %.1f cm | vehicle A=%d B=%d total=%lu | lamp %s %s\n",
                  STATE_NAMES[state], (int)camelOn, camelDir[0] ? camelDir : "-", (int)gA.raw, (int)gB.raw,
                  uA.dist < FAR_CM ? uA.dist : -1.0f, uB.dist < FAR_CM ? uB.dist : -1.0f,
                  (int)vehNowA, (int)vehNowB, (unsigned long)vehCount, outSig, hazardText);
  }

  if (resetRequested) {
    resetRequested = false;
    doReset("dashboard");
  }
  updateOutputs(now);
  delay(1);
}
