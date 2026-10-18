# Urutan pengujian hardware — Camel Guard firmware v2.3.1 (gerbang unta 2× IR + PIR · 2× HC-SR04 mobil di titik 400 m · 2× INA219 daya)

Checklist untuk upload pertama dan pengujian di maket highway. Urutannya dari yang paling aman ke yang paling
kompleks: kalau ada tahap gagal, **berhenti di situ** dan catat pesan Serial Monitor (115200 baud).
Kode tes (T-xx) sama dengan Testing Plan di proposal v3.3.

---

## ⚠ Keselamatan
- Semua tegangan rendah: satu adaptor USB 5 V untuk seluruh elektronik, tanpa listrik PLN atau 12 V.
- **ECHO HC-SR04 = 5 V** → wajib lewat pembagi 1 kΩ / 2 kΩ sebelum ke GPIO 33 dan 27. Ukur dulu dengan multimeter
  (harus ± 3,3 V saat ECHO HIGH) sebelum menyambung ke ESP32.
- Modul IR (FC-51) dan kedua 74HC595 memakai **3,3 V**. HC-SR04, PIR HC-SR501 dan buzzer memakai 5 V (pin VIN);
  keluaran (OUT) PIR HC-SR501 = 3,3 V, jadi langsung ke GPIO 4 tanpa pembagi. Ukur dulu: OUT PIR saat ada gerakan ≤ 3,3 V.
- Solder, lem tembak dan cutter hanya dengan pengawasan guru.

---

## Tahap 0 — Persiapan & rewiring dari firmware 2.2 (15 menit)
- [ ] Laptop tersambung ke WiFi 2,4 GHz WPA/WPA2-Personal (hotspot HP). WiFi Enterprise sekolah tidak bisa.
- [ ] Jalankan `START_DASHBOARD.bat`, jawab **Y** untuk aturan firewall (TCP 5100 + UDP 45811).
- [ ] Cek `firmware/camel_guard_esp32/config.h`: `WIFI_SSID` / `WIFI_PASS` sudah terisi otomatis. Pin sensor sekarang ada
      di `sensors_config.h` (nama sensor lama di `config.h` tidak dipakai lagi).
- [ ] Rangkaian dua 74HC595: Q7′ (pin 9) chip #1 → DS (pin 14) chip #2; SH_CP (11) dan ST_CP (12) kedua chip disambung.
- [ ] **HC-SR04 = sensor kendaraan** (kabel tetap, hanya posisi berubah): A di arah datang sisi A — TRIG → D32, ECHO
      (lewat pembagi) → D33; B di arah datang sisi B — TRIG → D26, ECHO (lewat pembagi) → D27. VCC → VIN (5 V).
      Pasang di **titik "400 m"** tiap arah datang (sebelum rambu 300 m), **di samping lajur, memancar melintang lajur**
      setinggi badan mobil mainan. Sensor ini hanya mendeteksi mobil yang lewat, bukan jarak mobil ke penyeberangan.
- [ ] **IR = sensor unta** (kabel tetap D13 / D14): IR A → D13 di tepi sisi A penyeberangan, IR B → D14 di tepi sisi B.
      VCC → 3V3, GND → GND.
- [ ] **PIR baru**: HC-SR501 di median, di tengah antara IR A dan IR B, menghadap ke penyeberangan. OUT → **D4**,
      VCC → VIN (5 V), GND → GND. Potensiometer *time* ke minimum (putar penuh berlawanan jarum jam, ± 3 dtk),
      jumper trigger di **H** (repeat). (AM312: VCC 3,3 V, tanpa potensiometer.)
- [ ] Panel surya + baterai (opsional, tahap 9): dua INA219, SDA → D17, SCL → D16, VCC → 3V3, GND bersama.
      Modul #2 dijumper pad A0 (alamat 0x41); modul #1 dibiarkan (0x40).

## Tahap 1 — Kompilasi (5 menit)
- [ ] `FIRMWARE_TOOLS.bat` → menu 1, atau Settings → Firmware & Ports → **Compile**.
- [ ] Hasil: `Compile OK`, flash ± 86 %, RAM ± 17 %. Tidak ada library tambahan yang perlu diinstal.

## Tahap 2 — Upload & boot (10 menit)
- [ ] Upload (menu 2 / tombol Upload di dashboard). Tutup Serial Monitor Arduino dulu.
- [ ] Serial: `=== Camel Guard ESP32 v2.3.1 ===`. Pengaturan lama tetap dipakai
      (`Settings of firmware 2.1.x / 2.2.x kept ... vehicle <= 15 cm, PIR window 3 s`).
- [ ] Serial juga menampilkan `Camel gate: IR A on (D13), IR B on (D14), PIR on (D4)` dan
      `Vehicle sensors (HC-SR04, 400 m points): A D32/D33, B D26/D27 · car when <= 15 cm across the lane`.
- [ ] Self-test: LED menyala bergantian chip #1 = sisi A (merah, kuning, hijau, 300 m, 200 m, 100 m, rambu unta), lalu
      chip #2 = sisi B dengan urutan yang sama. Urutan salah? Periksa kabel.
      Sisi B tidak menyala sama sekali? Periksa sambungan Q7′ chip #1 → DS chip #2.
- [ ] Buzzer berbunyi singkat sekali. Lampu kuning tetap 3 dtk, lalu hijau (CLEAR).
- [ ] **Tunggu 30–60 dtk** sebelum menguji unta: PIR HC-SR501 perlu waktu untuk stabil setelah dinyalakan.
- [ ] Dashboard: kartu baru muncul di Overview (`camel-guard-XXXX`). Beri nama & lokasi di Settings → General.

## Tahap 3 — Output (T-10, 15 menit)
Settings → **Output Test**, durasi 10 dtk:
- [ ] Test Traffic Signals → kedua lampu: hijau 2 dtk → hijau berkedip 3 dtk → kuning 3 dtk → merah 2 dtk, berulang
      (dashboard otomatis menjalankannya minimal satu putaran penuh = 10 dtk).
- [ ] Test Warning Signs → rambu unta dan keenam rambu 300/200/100 m berkedip.
- [ ] Test Each LED → satu keluaran 74HC595 menyala per detik (14 detik). Dashboard menulis keluaran mana yang
      seharusnya menyala ("Chip #1 Q3 (pin 3) — Warning sign 300 m A"); cocokkan dengan LED yang menyala.
- [ ] Test Buzzer → bip 0,5 dtk / dtk.

## Tahap 4 — Sensor mobil HC-SR04 di titik 400 m (T-01, 20 menit)
Buka Settings → **Sensors → Sensors Now** dan kartu **Vehicle Sensors At The 400 m Points (HC-SR04)**.
- [ ] Lajur kosong: **Vehicle HC-SR04 A / B** = **Lane Empty** (tidak ada benda dalam jangkauan); tidak ada fault
      `US-A` / `US-B`. Terbaca angka terus padahal lajur kosong? Sensor melihat benda di seberang lajur — miringkan sedikit
      atau perkecil *Detection Limit*.
- [ ] Muncul `US-A` / `US-B` (sama sekali tidak ada pulsa echo)? Cek TRIG / ECHO (jangan tertukar), VCC 5 V, GND bersama,
      dan pembagi 1 kΩ / 2 kΩ pada ECHO.
- [ ] Atur **Detection Limit (Lane Width)** sedikit di bawah lebar lajur (default 15 cm) → Save → status "Applied". Benda
      di seberang lajur terbaca ≤ nilai ini? Fault `CAR-A` / `CAR-B` muncul setelah 2 menit — perkecil nilainya.
- [ ] Ukur waktu mobil mainan dari sensor 400 m ke penyeberangan; atur **Vehicle Approach Time** ke nilai itu
      (default 8 dtk; perangkat yang di-update dari 2.2 masih memakai 3 dtk — ubah di Settings → General).
- [ ] T-01: tahan balok di 5 / 10 / 15 / 20 cm dari tiap sensor: jarak sesuai ± 1 cm (10 kali tiap jarak).

## Tahap 5 — Gerbang unta: IR + PIR (T-02, 20 menit)
- [ ] Gerbang kosong: **Camel IR A / B = Clear**, **PIR = No Motion** (tangan menjauh dari PIR). IR Blocked terus?
      Putar potensiometer modul itu (berlawanan jarum jam = jangkauan lebih pendek) sampai LED OUT di modul padam.
- [ ] T-02: lewatkan tangan / figur di depan IR A (10 kali) dan IR B (10 kali) → Blocked hanya selama ada benda.
- [ ] T-02: gerakkan tangan di depan PIR (10 kali) → **Motion**, lalu kembali **No Motion** dalam ± 3 dtk. Tetap Motion
      lebih lama? Potensiometer *time* belum di minimum. Terlalu sensitif / tidak bereaksi? Atur potensiometer *sens*.
- [ ] Live Road View: berkas IR A / IR B menyala merah muda saat terhalang, kipas ungu PIR menyala saat ada gerakan.

## Tahap 6 — Deteksi unta, arah & rambu peringatan (T-03 … T-05, T-08, T-09, 35 menit)
Catat hasil di tabel Test Record proposal:
- [ ] T-03: jalankan unta dari sisi A ke sisi B melewati gerbang (20 kali) → Serial `[DETECT] Camel CONFIRMED (IR A + PIR),
      coming from side A`, dashboard **CAMEL · From Side A**, dihitung **satu** kali (Settings → Device → Camels Since Boot).
- [ ] T-03: ulangi dari sisi B ke sisi A (20 kali) → **From Side B**, dihitung satu kali.
- [ ] Setiap kali (T-04): keenam rambu 300/200/100 m berkedip bergantian (wig-wag) ≤ 0,5 dtk, rambu unta berkedip,
      lampu: hijau berkedip → kuning → merah.
- [ ] T-05: tutup PIR (kertas / karton), lalu halangi IR A dan IR B dengan tangan atau mobil mainan (10 kali tiap sisi)
      → **tidak ada peringatan**; Serial `IR A without PIR motion within 3 s - not confirmed, ignored`; Log mencatat
      "Camel sensor: IR without PIR — ignored"; Settings → Sensors → **IR Without PIR (Ignored)** bertambah.
- [ ] T-08: Settings → General → **PIR Confirmation Window** = 1 dtk; halangi IR A lalu gerakkan tangan di depan PIR
      ± 2 dtk kemudian → tidak terkonfirmasi. Kembalikan ke 3 dtk → terkonfirmasi. Status "Applied".
- [ ] T-09: unta masuk hanya dari sisi B (10 kali) → tetap WARNING, dashboard menunjukkan **From Side B**.
- [ ] Settings → Sensors → **Camel Gate Sensors Fitted**: PIR = Not Fitted → satu IR saja sudah memicu WARNING (tanpa PIR).
      Kembalikan ke **Fitted**.

## Tahap 7 — Sensor kendaraan & fault (T-06, T-07, 25 menit)
- [ ] T-06: gulirkan mobil melewati sensor 400 m sisi A (20 kali), lalu sisi B (20 kali) → tabel Recent Vehicles dan
      jumlah kendaraan A/B bertambah tepat satu per mobil, di sisi yang benar. Terhitung dua kali? Arahkan sensor
      tegak lurus melintang lajur / perkecil *Detection Limit*.
- [ ] Mobil mainan lewat di depan IR unta tanpa gerakan di PIR → tidak dianggap unta (lihat T-05).
- [ ] T-07: cabut kabel ECHO sensor kendaraan A → fault **US-A** muncul dalam ± 2 dtk; pasang kembali → fault hilang.
      Halangi IR A terus-menerus > 2 menit → fault **OBJ-A**. Gerakan di depan PIR > 2 menit → fault **PIR**.
- [ ] Settings → Sensors → Camel Gate Sensors Fitted: IR B = Not Fitted → IR B diabaikan, IR A + PIR tetap bekerja.
      Kembalikan ke **Fitted**.

## Tahap 8 — Urutan lampu & DANGER (T-11 … T-15, 25 menit)
- [ ] Rekam video lampu A dan B saat unta masuk gerbang (10 kali): hijau berkedip 3 dtk ± 0,2 → kuning 3 dtk ± 0,2 → merah;
      tidak pernah melompat fase, A dan B selalu sama. Setelah gerbang kosong: merah ≥ 2 dtk lalu hijau.
- [ ] T-12: unta di gerbang + mobil lewat sensor 400 m sisi A → **DANGER**: rambu 300/200/100 m + rambu unta **sisi A**
      berkedip cepat, rambu sisi B tetap berkedip biasa, buzzer bip cepat (lampu tetap mengikuti urutan ke merah).
      Ulangi dengan mobil di sisi B → hanya rambu sisi B yang berkedip cepat.
- [ ] Mobil lewat sensor 400 m **tanpa** unta di gerbang → hanya dihitung, rambu tetap mati.
- [ ] T-14: keluarkan unta dari gerbang → CLEAR setelah PIR kembali No Motion + hold time (5 dtk).
- [ ] T-15: satu mobil saja saat ada unta → DANGER ± approach time (default 8 dtk), lalu kembali WARNING.

## Tahap 9 — Sistem (T-16 … T-20)
- [ ] Matikan hotspot: peringatan lokal tetap jalan, dashboard menampilkan OFFLINE dalam 10 dtk.
- [ ] Nyalakan lagi: perangkat kembali online sendiri (discovery UDP).
- [ ] Ubah IR Confirmation, PIR Confirmation Window, Detection Limit (Lane Width), Hold Time, Approach Time dan durasi lampu
      dari dashboard → status "Applied".
- [ ] Overview → ALERT ALL → semua penyeberangan memperingatkan; STOP ALERT menghentikan.
- [ ] Jalankan 2 jam: tidak ada restart ("Device restarted" di log), tidak ada DANGER palsu, catat jumlah "IR without PIR".

## Tahap 10 — Daya: panel surya + baterai (T-22 … T-25, 30 menit)
Hanya bila INA219, TP4056, baterai 18650 dan panel surya sudah dipasang (tanpa INA219 kotak Power tidak muncul — itu normal).
**Pakai TP4056 dengan proteksi baterai dan jangan pernah menghubung-singkatkan baterai 18650.**
- [ ] T-22: Serial menampilkan `[POWER] INA219 at 0x40 found` dan `... 0x41 found`. Settings → Sensors → **Power Sensors**:
      keduanya menampilkan tegangan dan arus (bukan "Not Found"). Dashboard menampilkan 4 tile Power dan 4 grafik tambahan.
- [ ] T-22: bandingkan tegangan baterai di dashboard dengan multimeter (selisih ≤ 0,05 V) dan arus dengan multimeter seri
      (selisih ≤ 10 %).
- [ ] T-23: panel di bawah lampu / matahari → **Solar Panel** menampilkan tegangan ≥ 4,5 V, **Charging = CHARGING**, arus baterai
      positif. Tutup panel → **NO SUN**, status berubah ke **ON BATTERY** dengan arus negatif. Tanda terbalik? Tukar VIN+ / VIN−
      modul itu atau ubah `INA_*_INVERT` di `config.h`.
- [ ] T-23: baterai penuh (≥ 4,10 V, arus ≈ 0) saat ada sinar → **FULL**; Log mencatat "Battery charging", "Battery full",
      "Running on the battery".
- [ ] T-24: baterai lemah (< 3,45 V, bisa dengan catu daya variabel) → catatan merah "Battery low" di Dashboard dan event
      "Battery low" di Log; hilang di atas 3,60 V.
- [ ] T-25: cabut kabel SDA modul #2 → fault **INA-B** dalam ± 2 dtk; sambungkan kembali → fault hilang. Mulai ulang tanpa
      modul INA sama sekali → tidak ada fault, hanya kotak Power yang tersembunyi.

## Tahap 11 — Demonstrasi (T-21)
Skenario: gerbang kosong → mobil lewat tanpa unta (rambu mati) → unta dari sisi A → mobil dari sisi A (DANGER, rambu A cepat) → unta dari sisi B → mobil dari sisi B →
tangan lewat IR saja (diabaikan) → CLEAR. Semua state dan arah unta harus tampil benar di maket dan di dashboard.
