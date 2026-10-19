# Dashboard di cloud (Render) — Camel Guard

Dashboard yang sama (`dashboard/server.py`) bisa dijalankan di **Render** supaya bisa dibuka dari mana saja dan ESP32
mengirim data lewat internet. Dashboard lokal di laptop (`START_DASHBOARD.bat`) tetap bekerja seperti biasa — pilih
salah satu untuk ESP32.

> Keputusan peringatan tetap dibuat di ESP32: lampu, rambu dan buzzer tetap jalan walau internet atau Render mati.

## Yang perlu diketahui dulu
- **Login wajib.** Di cloud server menolak start tanpa `DASHBOARD_PASSWORD`. Browser menanyakan *username* (bebas, mis.
  `admin`) dan *password* (= `DASHBOARD_PASSWORD`).
- **Kunci perangkat.** ESP32 mengirim header `X-Device-Key`; server menolak telemetri tanpa kunci yang sama
  (`DEVICE_KEY`). Tanpa `DEVICE_KEY` siapa pun bisa mengirim data palsu — selalu isi.
- **Paket gratis Render:** layanan "tidur" setelah ± 15 menit tanpa trafik (ESP32 yang mengirim tiap detik membuatnya
  tetap bangun), start pertama setelah tidur ± 30–60 dtk. **Disk tidak permanen:** log/riwayat (`road_log.db`) hilang
  setiap kali layanan di-restart atau di-deploy ulang.
- **Tidak ada UDP discovery** lewat internet: alamat dashboard harus diisi manual di ESP32 (langkah 4).
- ESP32 memakai HTTPS ke Render **tanpa memeriksa sertifikat** (`setInsecure`) — cukup untuk maket sekolah, bukan untuk
  sistem jalan sungguhan.
- Tab Settings → Firmware & Ports (compile/upload, port, firewall) hanya berfungsi di laptop, tidak di cloud.

## 1. Akun Render
1. Buka https://render.com → **Get Started** → daftar dengan **GitHub** (akun SkillsHUB-Electronics).
2. Izinkan Render membaca repo `SkillsHUB-Electronics/camel-crossing-v1`.

## 2. Buat layanan dari `render.yaml`
1. Render Dashboard → **New** → **Blueprint** → pilih repo `camel-crossing-v1` (branch `main`).
2. Render membaca `render.yaml` dan membuat web service **camel-guard-dashboard** (paket Free, `python dashboard/server.py`).
3. Render meminta dua nilai rahasia — isi sendiri, **jangan di-commit ke GitHub**:
   - `DASHBOARD_PASSWORD` — password login dashboard (min. 12 karakter).
   - `DEVICE_KEY` — kunci acak untuk ESP32 (mis. 24 huruf/angka acak).
4. **Apply** → tunggu sampai status **Live**. Alamatnya mis. `https://camel-guard-dashboard.onrender.com`
   (nama bisa berbeda bila sudah dipakai orang — lihat di halaman layanan).
5. Buka alamat itu → masukkan username bebas + `DASHBOARD_PASSWORD`. Dashboard tampil (masih "Waiting For The Device").

Ganti password nanti: layanan → **Environment** → ubah `DASHBOARD_PASSWORD` → **Save** (layanan restart otomatis).

## 3. Kunci perangkat di firmware
Tambahkan satu baris di **`firmware/camel_guard_esp32/config.h`** (file ini tidak ikut ke GitHub), nilainya **sama persis**
dengan `DEVICE_KEY` di Render:

```c
#define DEVICE_KEY "kunci-acak-yang-sama-dengan-render"
```

Lalu upload firmware (Settings → Firmware & Ports → Upload, atau `FIRMWARE_TOOLS.bat` menu 2).

## 4. Arahkan ESP32 ke Render
Selagi ESP32 masih terhubung ke dashboard **lokal** di laptop:
1. Settings → **Network** → **Dashboard Address**: isi `camel-guard-dashboard.onrender.com:443`
   (nama host Render + `:443`; `https://` boleh ikut ditempel, akan dibuang otomatis) → **Apply**.
2. Port 443 membuat ESP32 memakai **HTTPS**. Dalam beberapa detik perangkat hilang dari dashboard lokal dan muncul di
   dashboard Render.

Kembali ke dashboard lokal: isi Dashboard Address di dashboard Render dengan IP laptop (mis. `192.168.1.20:5100`), atau
kosongkan lewat halaman setup AP. Bila Render tidak bisa dihubungi 5 kali berturut-turut, ESP32 juga mencari dashboard
lokal lewat UDP broadcast dan pindah ke sana bila ada.

## 5. Cek
- Dashboard Render menampilkan perangkat **Online**, telemetri tiap ± 1–2 dtk (HTTPS lewat internet lebih lambat dari LAN).
- Render → layanan → **Logs**: baris `Camel Guard dashboard (cloud mode) on port ... - login required`.
- Tanpa login: halaman meminta password; `https://…onrender.com/healthz` menjawab `{"ok":true}` (dipakai Render).
- Serial Monitor ESP32: `[NET]  Dashboard connected OK (https://…/api/telemetry)`.

## Jalankan mode cloud di laptop (uji)
```powershell
$env:CG_CLOUD = "1"; $env:DASHBOARD_PASSWORD = "uji123"; $env:DEVICE_KEY = "kunci"; $env:PORT = "5180"; python dashboard\server.py
```
