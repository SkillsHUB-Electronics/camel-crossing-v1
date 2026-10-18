"""
Project-wide settings shared by the dashboard, the .bat tools and the firmware build.

camel_guard.ini (project root, synced through OneDrive):

    [ports]
    dashboard = 5100     ; TCP — the ESP32 posts telemetry here, the browser opens it
    discovery = 45811    ; UDP — compiled into the firmware as DISCOVERY_PORT

    [com]
    LAPTOP-NAME = COM7   ; USB port of the ESP32, per PC (every PC numbers COM ports differently)

Command line (used by the .bat files):
    python project_settings.py get dashboard|discovery|com
"""
import base64
import configparser
import os
import re
import socket
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INI = os.path.join(ROOT, "camel_guard.ini")
SKETCH_DIR = os.path.join(ROOT, "firmware", "camel_guard_esp32")
CONFIG_H = os.path.join(SKETCH_DIR, "config.h")
SKETCH = os.path.join(SKETCH_DIR, "camel_guard_esp32.ino")

DEFAULTS = {"dashboard": 5100, "discovery": 45811}
PORT_MIN, PORT_MAX = 1024, 65535
RULE_NAME = "Camel Guard Dashboard"


# ---------------------------------------------------------------- helpers
def run(cmd, timeout=60):
    """Run a command, return stdout text ('' on failure). stdin is never inherited."""
    try:
        r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for enc in ("utf-8", "oem", "mbcs"):
        try:
            return r.stdout.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return r.stdout.decode("utf-8", errors="replace")


def powershell(script, timeout=60):
    wrapped = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script
    return run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", wrapped], timeout).strip()


def computer():
    return os.environ.get("COMPUTERNAME") or socket.gethostname()


# ---------------------------------------------------------------- camel_guard.ini
def _read():
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cp.optionxform = str          # keep COMPUTERNAME keys as they are
    cp.read(INI, encoding="utf-8")
    return cp


def load():
    cp = _read()
    out = {}
    for key, default in DEFAULTS.items():
        try:
            v = int(cp.get("ports", key, fallback=str(default)))
        except ValueError:
            v = default
        out[key] = v if PORT_MIN <= v <= PORT_MAX else default
    out["com"] = cp.get("com", computer(), fallback="").strip().upper()
    return out


def last_upload():
    """What was flashed last: {'fw', 'discovery', 'port', 'at'} — tells whether the device
    still needs a firmware upload after the discovery port changed."""
    cp = _read()
    if not cp.has_section("firmware"):
        return {}
    out = dict(cp.items("firmware"))
    try:
        out["discovery"] = int(out.get("discovery", 0)) or None
    except ValueError:
        out["discovery"] = None
    return out


def record_upload(fw, discovery, port):
    from datetime import datetime
    cp = _read()
    if not cp.has_section("firmware"):
        cp.add_section("firmware")
    cp.set("firmware", "fw", str(fw or ""))
    cp.set("firmware", "discovery", str(discovery or ""))
    cp.set("firmware", "port", str(port or ""))
    cp.set("firmware", "at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    _write(cp)


def boards():
    """{COMx: MAC} of the boards seen on this PC's USB ports (learned from uploads and checks)."""
    cp, sec = _read(), "boards." + computer()
    return {k.upper(): v for k, v in cp.items(sec)} if cp.has_section(sec) else {}


def record_board(com, mac):
    """COM numbers differ per PC and a board can move to another port: one MAC per port, one port per MAC."""
    if not com or not mac:
        return
    cp, sec = _read(), "boards." + computer()
    if not cp.has_section(sec):
        cp.add_section(sec)
    for k, v in list(cp.items(sec)):
        if v.upper() == mac.upper() and k.upper() != com.upper():
            cp.remove_option(sec, k)
    cp.set(sec, com.upper(), mac.upper())
    _write(cp)


def _write(cp):
    with open(INI, "w", encoding="utf-8") as fh:
        fh.write("; Camel Guard project settings - edited by the dashboard (Settings > Firmware & ports)\n")
        fh.write("; and by FIRMWARE_TOOLS.bat. [com] is per PC because COM numbers differ between PCs.\n\n")
        cp.write(fh)


def save(dashboard=None, discovery=None, com=None):
    cp = _read()
    for section in ("ports", "com"):
        if not cp.has_section(section):
            cp.add_section(section)
    cur = load()
    cp.set("ports", "dashboard", str(dashboard if dashboard is not None else cur["dashboard"]))
    cp.set("ports", "discovery", str(discovery if discovery is not None else cur["discovery"]))
    if com is not None:
        if com:
            cp.set("com", computer(), com.strip().upper())
        elif cp.has_option("com", computer()):
            cp.remove_option("com", computer())
    _write(cp)
    return load()


# ---------------------------------------------------------------- port checks
def valid_port(v):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return None
    return v if PORT_MIN <= v <= PORT_MAX else None


def port_owner(port, proto):
    """Name and pid of the process holding a local TCP (listening) or UDP port, or None."""
    if proto == "tcp":
        q = f"Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue"
    else:
        q = f"Get-NetUDPEndpoint -LocalPort {int(port)} -ErrorAction SilentlyContinue"
    out = powershell(f"$e = {q} | Select-Object -First 1; if ($e) {{ $p = Get-Process -Id $e.OwningProcess "
                     f"-ErrorAction SilentlyContinue; \"$($e.OwningProcess)|$($p.ProcessName)\" }}")
    if "|" not in out:
        return None
    pid, name = out.split("|", 1)
    try:
        return {"pid": int(pid), "name": name or "unknown"}
    except ValueError:
        return None


def check_port(port, proto, own_pid=None):
    """{'ok', 'state': free|ours|busy|invalid, 'owner', 'text'} — own_pid = this dashboard."""
    p = valid_port(port)
    if p is None:
        return {"ok": False, "state": "invalid", "owner": None,
                "text": f"use a number between {PORT_MIN} and {PORT_MAX}"}
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM if proto == "tcp" else socket.SOCK_DGRAM)
    try:
        s.bind(("0.0.0.0", p))   # no SO_REUSEADDR: on Windows it would hide a conflict
        return {"ok": True, "state": "free", "owner": None, "text": "free"}
    except OSError:
        pass
    finally:
        s.close()
    owner = port_owner(p, proto)
    if owner and own_pid and owner["pid"] == own_pid:
        return {"ok": True, "state": "ours", "owner": owner, "text": "used by this dashboard"}
    name = f'{owner["name"]} (pid {owner["pid"]})' if owner else "another program"
    return {"ok": False, "state": "busy", "owner": owner, "text": f"in use by {name}"}


# ---------------------------------------------------------------- firewall
def firewall_status(tcp, udp):
    """Which of our two inbound allow rules exist (any rule whose name starts with RULE_NAME)."""
    out = powershell(
        f"$r = Get-NetFirewallRule -DisplayName '{RULE_NAME}*' -ErrorAction SilentlyContinue |"
        " Where-Object { $_.Enabled -eq 'True' -and $_.Action -eq 'Allow' -and $_.Direction -eq 'Inbound' };"
        " $f = $r | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue;"
        f" $t = [bool]($f | Where-Object {{ $_.Protocol -eq 'TCP' -and $_.LocalPort -eq '{int(tcp)}' }});"
        f" $u = [bool]($f | Where-Object {{ $_.Protocol -eq 'UDP' -and $_.LocalPort -eq '{int(udp)}' }});"
        " \"$t|$u\"")
    t, _, u = out.partition("|")
    return {"tcp": t.strip() == "True", "udp": u.strip() == "True"}


def firewall_apply(tcp, udp):
    """Replace all our rules with TCP `tcp` + UDP `udp` (local subnet only). Shows ONE admin prompt."""
    inner = (f"Get-NetFirewallRule -DisplayName '{RULE_NAME}*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule;"
             f"New-NetFirewallRule -DisplayName '{RULE_NAME} (TCP {int(tcp)})' -Direction Inbound -Protocol TCP "
             f"-LocalPort {int(tcp)} -RemoteAddress LocalSubnet -Action Allow -Profile Any | Out-Null;"
             f"New-NetFirewallRule -DisplayName '{RULE_NAME} (UDP {int(udp)})' -Direction Inbound -Protocol UDP "
             f"-LocalPort {int(udp)} -RemoteAddress LocalSubnet -Action Allow -Profile Any | Out-Null")
    encoded = base64.b64encode(inner.encode("utf-16-le")).decode()
    powershell("try { Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
               f"-ArgumentList '-NoProfile','-EncodedCommand','{encoded}' }} catch {{ }}", timeout=180)
    return firewall_status(tcp, udp)


def python_blocked():
    """True if Windows Firewall has an enabled inbound BLOCK rule for python (wins over allow rules)."""
    return powershell(
        "$b = Get-NetFirewallRule -Direction Inbound -Action Block -Enabled True -ErrorAction SilentlyContinue |"
        " Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | Where-Object { $_.Program -like '*python*' };"
        "if ($b) {'yes'} else {'no'}") == "yes"


# ---------------------------------------------------------------- firmware sources
def sketch_version():
    try:
        with open(SKETCH, encoding="utf-8") as fh:
            m = re.search(r'^#define\s+FW_VERSION\s+"([^"]+)"', fh.read(), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def config_h_ports():
    try:
        with open(CONFIG_H, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    out = {}
    for key, name in (("dashboard", "SERVER_PORT"), ("discovery", "DISCOVERY_PORT")):
        m = re.search(rf"^#define\s+{name}\s+(\d+)", text, re.M)
        if m:
            out[key] = int(m.group(1))
    return out


def sync_config_h(ports=None):
    """Write the saved ports into config.h (SERVER_PORT = first-boot default, DISCOVERY_PORT = compiled in).
    Returns the list of changed #defines."""
    ports = ports or load()
    with open(CONFIG_H, encoding="utf-8", newline="") as fh:
        text = fh.read()
    changed = []
    for key, name in (("dashboard", "SERVER_PORT"), ("discovery", "DISCOVERY_PORT")):
        pat = rf"^(#define\s+{name}\s+)(\d+)"
        m = re.search(pat, text, re.M)
        if m and int(m.group(2)) != ports[key]:
            text = re.sub(pat, lambda mm: mm.group(1) + str(ports[key]), text, count=1, flags=re.M)
            changed.append(name)
    if changed:
        with open(CONFIG_H, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    return changed


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "get":
        print(load().get(sys.argv[2], ""))
    else:
        print(__doc__)
