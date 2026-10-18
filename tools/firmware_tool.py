"""
Compile / upload the ESP32 firmware and manage the project ports.
Used by FIRMWARE_TOOLS.bat (menu) and by the dashboard (Settings > Firmware & ports).

  python firmware_tool.py              interactive menu
  python firmware_tool.py compile
  python firmware_tool.py upload [COMx]
  python firmware_tool.py check  [COMx]
  python firmware_tool.py ports

Uses the arduino-cli that ships inside Arduino IDE 2, with the IDE's own configuration, so the
same ESP32 core and libraries are used as when uploading from the IDE. Nothing extra to install.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_settings as ps  # noqa: E402

FQBN = "esp32:esp32:esp32"          # "ESP32 Dev Module"
BUILD_DIR = os.path.join(tempfile.gettempdir(), "camel_guard_build")   # short path: Windows 260-char limit
LIBS = []   # nothing beyond the ESP32 core (WiFi, WebServer, Preferences are built in)
# USB-serial bridges used on ESP32 boards: CP210x, CH340/CH9102, FTDI, Espressif native USB
ESP_VIDS = {"10C4": "Silicon Labs CP210x", "1A86": "WCH CH340/CH9102", "0403": "FTDI", "303A": "Espressif USB",
            "067B": "Prolific PL2303"}


# ---------------------------------------------------------------- arduino-cli
def find_cli():
    cands = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Arduino IDE"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Arduino IDE"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Arduino IDE"),
    ]
    for base in cands:
        exe = os.path.join(base, "resources", "app", "lib", "backend", "resources", "arduino-cli.exe")
        if os.path.isfile(exe):
            return exe
    return shutil.which("arduino-cli")


def cli_base():
    exe = find_cli()
    if not exe:
        return None
    cmd = [exe]
    cfg = os.path.join(os.path.expanduser("~"), ".arduinoIDE", "arduino-cli.yaml")
    if os.path.isfile(cfg):
        cmd += ["--config-file", cfg]   # the IDE's sketchbook, libraries and board URLs
    return cmd


def cli_json(args, timeout=60):
    base = cli_base()
    if not base:
        return None
    try:
        return json.loads(ps.run(base + args + ["--format", "json"], timeout) or "null")
    except json.JSONDecodeError:
        return None


def environment():
    """What is installed: arduino-cli, ESP32 core, libraries."""
    exe = find_cli()
    env = {"cli": exe, "cliVersion": None, "core": None, "libs": {n: None for n in LIBS}, "fqbn": FQBN}
    if not exe:
        return env
    v = cli_json(["version"]) or {}
    env["cliVersion"] = v.get("VersionString") or v.get("version")
    cores = cli_json(["core", "list"]) or {}
    for c in (cores.get("platforms") if isinstance(cores, dict) else cores) or []:
        if c.get("id") == "esp32:esp32":
            env["core"] = c.get("installed_version") or c.get("installed")
    libs = cli_json(["lib", "list"]) or {}
    for item in (libs.get("installed_libraries") if isinstance(libs, dict) else libs) or []:
        lib = item.get("library", item)
        if lib.get("name") in env["libs"]:
            env["libs"][lib["name"]] = lib.get("version")
    return env


def stream(cmd, on_line, timeout=900):
    """Run a command, pass each output line to on_line, return the exit code."""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        on_line(f"[error] could not start {cmd[0]}: {e}")
        return -1
    t0 = time.time()
    last_pct = -1
    for raw in iter(p.stdout.readline, b""):
        # esptool redraws its progress bar with '\r', so one "line" can hold many updates
        for line in raw.decode("utf-8", errors="replace").split("\r"):
            line = line.rstrip()
            m = re.search(r"Writing at 0x[0-9a-f]+.*?(\d+(?:\.\d+)?)\s*%", line)
            if m:   # esptool progress: report every 20 %
                pct = int(float(m.group(1)))
                if pct < last_pct:          # next image (bootloader, partitions, app)
                    last_pct = -1
                if pct // 20 != last_pct // 20 or (pct == 100 and last_pct != 100):
                    on_line(f"  writing… {pct}%")
                last_pct = pct
                continue
            if line.strip():
                on_line(line)
        if time.time() - t0 > timeout:
            p.kill()
            on_line("[error] timed out")
            return -1
    return p.wait()


def install_missing(on_line=print):
    base = cli_base()
    if not base:
        on_line("[error] arduino-cli not found - install Arduino IDE 2 first")
        return False
    env = environment()
    ok = True
    if not env["core"]:
        on_line("Installing the ESP32 core (large download, several minutes)…")
        ok &= stream(base + ["core", "update-index"], on_line) == 0
        ok &= stream(base + ["core", "install", "esp32:esp32"], on_line) == 0
    missing = [n for n, v in env["libs"].items() if not v]
    if missing:
        on_line("Installing libraries: " + ", ".join(missing))
        ok &= stream(base + ["lib", "install"] + missing, on_line) == 0
    if ok:
        on_line("All tools are installed.")
    return ok


# ---------------------------------------------------------------- COM ports
def list_ports():
    """[{port, name, vid, esp}] for every COM port Windows knows about."""
    out = ps.powershell(
        "Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '\\(COM\\d+\\)' } |"
        " ForEach-Object { \"$($_.Name)|$($_.PNPDeviceID)\" }")
    ports = []
    for line in out.splitlines():
        name, _, pnp = line.partition("|")
        m = re.search(r"\((COM\d+)\)", name)
        if not m:
            continue
        vid = (re.search(r"VID_([0-9A-F]{4})", pnp, re.I) or [None, None])[1]
        vid = vid.upper() if vid else None
        ports.append({"port": m.group(1), "name": name.replace(f" ({m.group(1)})", "").strip(),
                      "vid": vid, "esp": vid in ESP_VIDS})
    ports.sort(key=lambda p: (not p["esp"], int(p["port"][3:])))
    return ports


def pick_port(explicit=None):
    """The COM port to use: explicit > saved for this PC > the only ESP32-looking port."""
    if explicit:
        return explicit.upper()
    saved = ps.load()["com"]
    ports = list_ports()
    if saved and any(p["port"] == saved for p in ports):
        return saved
    esp = [p for p in ports if p["esp"]]
    return esp[0]["port"] if len(esp) == 1 else None


# ---------------------------------------------------------------- build / upload / check
def compile_sketch(on_line=print):
    base = cli_base()
    if not base:
        on_line("[error] arduino-cli not found - install Arduino IDE 2 (it contains arduino-cli)")
        return {"ok": False}
    changed = ps.sync_config_h()
    if changed:
        on_line(f"config.h updated from camel_guard.ini: {', '.join(changed)}")
    ver = ps.sketch_version()
    on_line(f"Compiling firmware {ver} for {FQBN}…")
    res = {"ok": False, "version": ver}

    def grab(line):
        m = re.search(r"Sketch uses (\d+) bytes \((\d+)%\)", line)
        if m:
            res["flash"] = int(m.group(2))
        m = re.search(r"Global variables use (\d+) bytes \((\d+)%\)", line)
        if m:
            res["ram"] = int(m.group(2))
        on_line(line)

    code = stream(base + ["compile", "--fqbn", FQBN, "--build-path", BUILD_DIR, "--warnings", "default", ps.SKETCH_DIR], grab)
    res["ok"] = code == 0
    on_line("Compile OK" if res["ok"] else f"[error] compile failed (exit {code})")
    return res


def upload(port, on_line=print):
    base = cli_base()
    if not base:
        on_line("[error] arduino-cli not found")
        return {"ok": False}
    if not port:
        on_line("[error] no USB port selected")
        return {"ok": False}
    res = compile_sketch(on_line)
    if not res["ok"]:
        return res
    on_line(f"Uploading to {port} — the road warnings on the ESP32 pause for about 30–40 s…")
    code = stream(base + ["upload", "-p", port, "--fqbn", FQBN, "--input-dir", BUILD_DIR, ps.SKETCH_DIR], on_line)
    if code != 0:
        on_line(f"[error] upload failed (exit {code}). Is {port} open in the Arduino Serial Monitor or in the "
                "dashboard's 'Configure over USB'? Close it and try again.")
        return {**res, "ok": False}
    on_line("Upload OK — checking that the new firmware starts…")
    chk = check_device(port, on_line, seconds=25)
    return {**res, "ok": True, "check": chk}


_SERIAL_PS = r"""
$p = New-Object System.IO.Ports.SerialPort '__PORT__', 115200
$p.DtrEnable = $false; $p.RtsEnable = $false; $p.Encoding = [Text.Encoding]::UTF8; $p.ReadTimeout = 200
try { $p.Open() } catch { "OPEN_ERROR: $($_.Exception.Message)"; exit }
$b = New-Object System.Text.StringBuilder; $t0 = Get-Date; $next = 2
while (((Get-Date) - $t0).TotalSeconds -lt __SECS__) {
  [void]$b.Append($p.ReadExisting())
  $el = ((Get-Date) - $t0).TotalSeconds
  if ($el -gt $next) { $p.Write("@info`n"); $next += 4 }
  if ($b.ToString() -match '@@\{"t":"info"') { Start-Sleep -Milliseconds 300; [void]$b.Append($p.ReadExisting()); break }
  Start-Sleep -Milliseconds 100
}
$p.Close()
$b.ToString()
"""


def check_device(port, on_line=print, seconds=12):
    """Read the device over USB: boot banner (if it just restarted) and an @info reply."""
    if not port:
        on_line("[error] no USB port selected")
        return {"ok": False}
    script = _SERIAL_PS.replace("__PORT__", port).replace("__SECS__", str(int(seconds)))
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    out = ps.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc], timeout=seconds + 30)
    if out.startswith("OPEN_ERROR"):
        on_line(f"[error] cannot open {port}: {out[11:].strip()} — close the Serial Monitor / USB tab using it")
        return {"ok": False}
    res = {"ok": False, "banner": None, "info": None}
    for line in out.splitlines():
        m = re.search(r"=== Camel Guard ESP32 v([\w.\-]+) ===", line)
        if m:
            res["banner"] = m.group(1)
        i = line.find('@@{"t":"info"')
        if i >= 0:
            try:
                res["info"] = json.loads(line[i + 2:])
            except json.JSONDecodeError:
                pass
    info = res["info"]
    if res["banner"]:
        on_line(f"Boot banner: v{res['banner']}")
    if info:
        res["ok"] = True
        net = f'WiFi "{info.get("ssid")}", IP {info.get("ip")}' if info.get("wifi") else \
            ("no WiFi — setup access point is on" if info.get("ap") else "no WiFi yet")
        on_line(f'Device answers: firmware {info.get("fw")}, name "{info.get("name")}", {net}, '
                f'dashboard {"connected" if info.get("dash") else "not connected yet"}')
    else:
        on_line("[warning] no answer to @info — firmware older than 2.0.2, or the board is still booting")
    return res


# ---------------------------------------------------------------- interactive menu (FIRMWARE_TOOLS.bat)
def _ask(prompt, default=""):
    try:
        v = input(f"{prompt}{f' [{default}]' if default != '' else ''}: ").strip()
    except EOFError:
        return default
    return v or default


def _choose_port():
    ports = list_ports()
    if not ports:
        print("No COM ports found. Plug in the ESP32 (USB data cable, not charge-only) and try again.")
        return None
    cur = pick_port()
    print("\nUSB ports on this PC:")
    for i, p in enumerate(ports, 1):
        tag = f"  <- {ESP_VIDS[p['vid']]} (ESP32?)" if p["esp"] else ""
        print(f"  {i}. {p['port']:6s} {p['name']}{tag}")
    ans = _ask("Choose a number or type a port like COM9", cur or "")
    if ans.isdigit() and 1 <= int(ans) <= len(ports):
        ans = ports[int(ans) - 1]["port"]
    ans = ans.upper()
    if not re.fullmatch(r"COM\d+", ans):
        print("Not a COM port.")
        return None
    if ans != ps.load()["com"]:
        ps.save(com=ans)
        print(f"Saved {ans} as this PC's ESP32 port.")
    return ans


def _ports_menu():
    cur = ps.load()
    print(f"\nCurrent ports: dashboard TCP {cur['dashboard']}, discovery UDP {cur['discovery']}, "
          f"USB {cur['com'] or '(not set)'}")
    new = {}
    for key, proto, label in (("dashboard", "tcp", "Dashboard port (TCP)"), ("discovery", "udp", "Discovery port (UDP)")):
        while True:
            v = _ask(label, cur[key])
            p = ps.valid_port(v)
            if p is None:
                print(f"  Use a number between {ps.PORT_MIN} and {ps.PORT_MAX}.")
                continue
            # the current port may be held by a running dashboard - that is expected
            chk = {"ok": True} if p == cur[key] else ps.check_port(p, proto)
            if not chk["ok"]:
                print(f"  Port {p} is {chk['text']}. Choose another one.")
                continue
            new[key] = p
            break
    com = _choose_port()
    saved = ps.save(dashboard=new.get("dashboard"), discovery=new.get("discovery"), com=com if com else None)
    changed = ps.sync_config_h(saved)
    print(f"\nSaved: dashboard {saved['dashboard']}, discovery {saved['discovery']}, USB {saved['com'] or '-'}")
    if changed:
        print(f"config.h updated: {', '.join(changed)}")
    fw = ps.firewall_status(saved["dashboard"], saved["discovery"])
    if not (fw["tcp"] and fw["udp"]):
        if sys.stdin and sys.stdin.isatty() and _ask("Update the Windows Firewall rule now? (admin prompt) y/n", "y").lower().startswith("y"):
            fw = ps.firewall_apply(saved["dashboard"], saved["discovery"])
        print("Firewall:", "OK" if fw["tcp"] and fw["udp"] else "NOT updated - run this again or use the dashboard")
    if saved["discovery"] != cur["discovery"]:
        print(">> The discovery port is compiled into the firmware: upload the firmware again (menu 2).")
    if saved["dashboard"] != cur["dashboard"] or saved["discovery"] != cur["discovery"]:
        print(">> Restart the dashboard (close its window, run START_DASHBOARD.bat) to use the new ports.")


def menu():
    while True:
        cur = ps.load()
        print("\n" + "=" * 60)
        print(" Camel Guard - firmware tools")
        print("=" * 60)
        print(f" Firmware in project: {ps.sketch_version()}   USB port: {cur['com'] or '(not set)'}")
        print(f" Ports: dashboard TCP {cur['dashboard']}, discovery UDP {cur['discovery']}")
        print("  1. Compile only (check for errors)")
        print("  2. Compile + upload to the ESP32")
        print("  3. Check the ESP32 over USB (version, WiFi, IP)")
        print("  4. Change ports (USB COM / dashboard / discovery)")
        print("  5. Install missing Arduino tools")
        print("  0. Exit")
        c = _ask("Choice", "0")
        if c == "1":
            compile_sketch()
        elif c == "2":
            port = _choose_port()
            if port:
                upload(port)
        elif c == "3":
            port = _choose_port()
            if port:
                check_device(port)
        elif c == "4":
            _ports_menu()
        elif c == "5":
            install_missing()
        elif c == "0":
            return 0


def main(argv):
    if not argv:
        return menu()
    cmd, arg = argv[0], (argv[1] if len(argv) > 1 else None)
    if cmd == "compile":
        return 0 if compile_sketch()["ok"] else 1
    if cmd == "upload":
        return 0 if upload(pick_port(arg))["ok"] else 1
    if cmd == "check":
        return 0 if check_device(pick_port(arg))["ok"] else 1
    if cmd == "ports":
        print(json.dumps({"saved": ps.load(), "com": list_ports()}, indent=2))
        return 0
    if cmd == "env":
        print(json.dumps(environment(), indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
