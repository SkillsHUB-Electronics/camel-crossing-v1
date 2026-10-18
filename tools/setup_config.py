"""
Auto-configure firmware/camel_guard_esp32/config.h from the laptop's current WiFi connection.
Called by START_DASHBOARD.bat (can also be run on its own).

  - WIFI_SSID   : the WiFi network the laptop is connected to
  - WIFI_PASS   : that network's saved password (asked if Windows won't reveal it)
  - SERVER_HOST : the laptop's IP address on that WiFi
  - SERVER_PORT : the dashboard port

Options:
  --dry-run   show what would change, write nothing, never add firewall rules
  --manual    type SSID / password yourself instead of auto-detecting
"""
import argparse
import getpass
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_settings  # noqa: E402

ROOT = project_settings.ROOT
CONFIG = project_settings.CONFIG_H
SKETCH = project_settings.SKETCH
_ports = project_settings.load()          # camel_guard.ini — edited in Settings > Firmware & ports
PORT = int(os.environ.get("PORT") or _ports["dashboard"])
DISCOVERY_PORT = _ports["discovery"]
RULE_NAME = project_settings.RULE_NAME
LINE = "=" * 64


# ---------------------------------------------------------------- helpers
def run(cmd):
    """Run a command and return stdout as text ('' on failure)."""
    try:
        # stdin=DEVNULL: helper commands must never swallow the user's answers
        r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for enc in ("utf-8", "oem", "mbcs"):
        try:
            return r.stdout.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return r.stdout.decode("utf-8", errors="replace")


def powershell(script):
    wrapped = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + script
    return run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", wrapped]).strip()


def ask_yes(question, default=True):
    hint = "[Y/n]" if default else "[y/N]"
    try:
        ans = input(f"{question} {hint} ").strip().lstrip("﻿").strip().lower()
    except EOFError:
        return default
    return default if not ans else ans.startswith("y")


def secret_input(prompt):
    if sys.stdin is not None and sys.stdin.isatty():
        return getpass.getpass(prompt)
    print(prompt, end="", flush=True)
    return (sys.stdin.readline() if sys.stdin else "").rstrip("\r\n")


def c_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def c_unescape(s):
    return re.sub(r"\\(.)", r"\1", s)


def mask(pw):
    return "(empty / open network)" if not pw else f"{'*' * min(len(pw), 12)} ({len(pw)} characters)"


# ---------------------------------------------------------------- WiFi detection
def wifi_interface():
    """SSID, band warning and auth type from `netsh wlan show interfaces`."""
    out = run(["netsh", "wlan", "show", "interfaces"])
    info = {"ssid": None, "high_band": False, "auth": ""}
    for line in out.splitlines():
        m = re.match(r"^\s*SSID\s*:\s*(.+?)\s*$", line)  # BSSID does not match (anchored)
        if m and info["ssid"] is None:
            info["ssid"] = m.group(1)
        if re.search(r":\s*[56](\.\d+)?\s*GHz", line):
            info["high_band"] = True
        m = re.match(r"^\s*Channel\s*:\s*(\d+)", line)
        if m and int(m.group(1)) > 14:
            info["high_band"] = True
        if re.search(r"(WPA|WEP|Open|Enterprise|802\.1X)", line, re.I) and ":" in line and not info["auth"]:
            key = line.split(":", 1)[0].strip().lower()
            if key.startswith("auth"):
                info["auth"] = line.split(":", 1)[1].strip()
    return info


def wifi_profile(ssid):
    """Read the saved profile (exact SSID name, password, auth) via `netsh wlan export profile key=clear`."""
    tmp = tempfile.mkdtemp(prefix="camelguard_wifi_")
    try:
        run(["netsh", "wlan", "export", "profile", f"folder={tmp}", "key=clear"])
        ns = {"w": "http://www.microsoft.com/networking/WLAN/profile/v1"}
        for path in glob.glob(os.path.join(tmp, "*.xml")):
            try:
                root = ET.parse(path).getroot()
            except ET.ParseError:
                continue
            names = [e.text for e in root.findall(".//w:SSIDConfig/w:SSID/w:name", ns) if e.text]
            profile_name = root.findtext("w:name", default="", namespaces=ns)
            if ssid not in names and ssid != profile_name:
                continue
            auth = root.findtext(".//w:authEncryption/w:authentication", default="", namespaces=ns)
            key = root.find(".//w:sharedKey", ns)
            password = None
            if key is not None and key.findtext("w:protected", default="", namespaces=ns).lower() == "false":
                password = key.findtext("w:keyMaterial", default="", namespaces=ns)
            elif auth.lower() == "open":
                password = ""
            return {"ssid": names[0] if names else ssid, "password": password, "auth": auth}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # never leave passwords on disk
    return None


def wifi_ip():
    """IPv4 address and network category of the connected WiFi adapter."""
    ps = (
        "$a = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' -and $_.NdisPhysicalMedium -eq 9 } | Select-Object -First 1;"
        "if ($a) {"
        " $ip = (Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '169.254*' } | Select-Object -First 1).IPAddress;"
        " $p = Get-NetConnectionProfile -InterfaceIndex $a.ifIndex -ErrorAction SilentlyContinue | Select-Object -First 1;"
        " [pscustomobject]@{ alias = $a.Name; ip = $ip; category = \"$($p.NetworkCategory)\" } | ConvertTo-Json -Compress }"
    )
    try:
        data = json.loads(powershell(ps) or "{}")
    except json.JSONDecodeError:
        data = {}
    if not data.get("ip"):  # fallback: address used for the default route
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            data["ip"] = s.getsockname()[0]
            data["fallback"] = True
        except OSError:
            pass
        finally:
            s.close()
    return data


# ---------------------------------------------------------------- firewall
def firewall(dry_run):
    # Rules from older versions (TCP only, or other port numbers) count as missing,
    # so the pair is recreated for the ports in camel_guard.ini.
    fw = project_settings.firewall_status(PORT, DISCOVERY_PORT)
    has_rule = fw["tcp"] and fw["udp"]
    if not has_rule and (fw["tcp"] or fw["udp"]):
        print(f"[INFO] The firewall rule does not cover TCP {PORT} + UDP {DISCOVERY_PORT} yet.")
    blocked = project_settings.python_blocked()

    if blocked:
        print("\n[WARNING] Windows Firewall has a BLOCK rule for Python (usually from clicking 'Cancel' on an earlier")
        print("          firewall popup). Block rules win over allow rules, so the ESP32 may not reach the dashboard.")
        print("          Fix: Start menu > 'Windows Defender Firewall with Advanced Security' > Inbound Rules >")
        print("          disable/delete the 'python' rules that have a red block icon, then run this again.")

    if has_rule:
        print(f"[OK] Firewall rule '{RULE_NAME}' exists (TCP {PORT} + UDP {DISCOVERY_PORT}).")
        return
    if dry_run:
        print(f"[dry-run] Firewall rule '{RULE_NAME}' is missing (would offer to add it).")
        return
    print(f"\nThe ESP32 must reach this laptop on TCP port {PORT}, and answer its discovery"
          f" broadcast on UDP port {DISCOVERY_PORT}.")
    if not (sys.stdin and sys.stdin.isatty()):
        print("[SKIPPED] Not running in an interactive window, so no admin prompt is shown.")
        print("          Double-click START_DASHBOARD.bat to be offered the firewall rule.")
        return
    if not ask_yes("Add a Windows Firewall rule for it (local network only)? A Windows admin prompt will appear.", True):
        print("[SKIPPED] No firewall rule added - the ESP32 will probably NOT reach the dashboard.")
        print("          Run this again and answer Y, or set this WiFi to 'Private' in Windows Settings.")
        return
    # Both halves must be present afterwards: an old TCP-only rule would otherwise make a
    # declined admin prompt look like success.
    fw = project_settings.firewall_apply(PORT, DISCOVERY_PORT)
    ok = fw["tcp"] and fw["udp"]
    print("[OK] Firewall rule added (TCP + UDP)." if ok else "[WARNING] Firewall rule was not added (admin prompt declined?).")


# ---------------------------------------------------------------- config.h
DEFINE_RE = r'^(#define\s+{name}\s+)"((?:[^"\\]|\\.)*)"'


def read_define(text, name):
    m = re.search(DEFINE_RE.format(name=name), text, re.M)
    return c_unescape(m.group(2)) if m else None


def write_define(text, name, value):
    pattern = DEFINE_RE.format(name=name)
    if not re.search(pattern, text, re.M):
        raise ValueError(f'#define {name} "..." not found in config.h')
    return re.sub(pattern, lambda m: f'{m.group(1)}"{c_escape(value)}"', text, count=1, flags=re.M)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--manual", action="store_true")
    args = ap.parse_args()

    print(LINE)
    print(" Camel Guard - automatic ESP32 configuration")
    print(LINE)
    print(f"Project folder: {ROOT}")
    if not os.path.isfile(CONFIG):
        print(f"[ERROR] config.h not found: {CONFIG}")
        return 1

    with open(CONFIG, encoding="utf-8", newline="") as fh:
        text = fh.read()
    current = {n: read_define(text, n) for n in ("WIFI_SSID", "WIFI_PASS", "SERVER_HOST")}
    m = re.search(r"^#define\s+SERVER_PORT\s+(\d+)", text, re.M)
    current["SERVER_PORT"] = m.group(1) if m else None

    # --- detect WiFi
    ssid = password = None
    iface = wifi_interface()
    if not args.manual:
        if iface["ssid"]:
            prof = wifi_profile(iface["ssid"])
            ssid = prof["ssid"] if prof else iface["ssid"]
            password = prof["password"] if prof else None
            auth = ((prof or {}).get("auth") or iface["auth"]).lower()
            print(f"[OK] Laptop is connected to WiFi: {ssid}")
            if auth in ("wpa", "wpa2") or "ent" in auth:  # profile XML: WPA/WPA2/WPA3ENT* = 802.1X
                print(LINE)
                print("[PROBLEM] This is an Enterprise (802.1X / username+password) network, e.g. campus or")
                print("          office WiFi. The ESP32 firmware supports WPA/WPA2-Personal networks only.")
                print("          Connect the laptop to a home router or a phone hotspot (2.4 GHz), then run again.")
                print(LINE)
                if ask_yes("Keep config.h unchanged and start the dashboard anyway (e.g. to use the simulator)?", True):
                    firewall(args.dry_run)
                    return 0
                return 1
            if iface["high_band"]:
                print("[WARNING] The laptop is on 5/6 GHz. The ESP32 only supports 2.4 GHz WiFi.")
                print("          Fine if the router also broadcasts this name on 2.4 GHz; otherwise use a 2.4 GHz network.")
        else:
            print("[WARNING] The laptop does not seem to be connected to WiFi.")

    if ssid is None:
        entered = input(f"WiFi SSID for the ESP32 (Enter = keep '{current['WIFI_SSID']}'): ").strip()
        ssid = entered or current["WIFI_SSID"]
    if password is None:
        if ssid == current["WIFI_SSID"] and current["WIFI_PASS"] not in (None, "YOUR_WIFI_PASSWORD"):
            print("Windows did not reveal the saved password.")
            entered = secret_input("WiFi password (typing is hidden; Enter = keep the one in config.h): ")
            password = entered if entered else current["WIFI_PASS"]
        else:
            if not args.manual:
                print("Windows did not reveal the saved password for this network.")
            password = secret_input(f"Type the WiFi password for '{ssid}' (typing is hidden): ")

    # --- detect IP
    net = wifi_ip()
    ip = net.get("ip")
    if not ip:
        print("[ERROR] Could not find this laptop's IP address. Is WiFi connected?")
        return 1
    if net.get("fallback"):
        print("[WARNING] WiFi adapter IP not found; using the default-route IP instead.")
    if net.get("category", "").lower() == "public":
        print("[INFO] Windows treats this WiFi as a 'Public' network; the firewall rule below still allows local devices.")

    new = {"WIFI_SSID": ssid, "WIFI_PASS": password, "SERVER_HOST": ip, "SERVER_PORT": str(PORT)}
    print()
    print(f"  WiFi SSID     : {ssid}")
    print(f"  WiFi password : {mask(password)}")
    print(f"  Server host   : {ip}  (this laptop{', adapter ' + net['alias'] if net.get('alias') else ''})")
    print(f"  Server port   : {PORT}")
    print()

    changed = [k for k in new if new[k] != current.get(k)]
    if not changed:
        print("[OK] config.h is already up to date - no need to re-upload the firmware.")
    else:
        labels = {"WIFI_SSID": "WiFi SSID", "WIFI_PASS": "WiFi password", "SERVER_HOST": "server IP", "SERVER_PORT": "server port"}
        if args.dry_run:
            print(f"[dry-run] Would update config.h: {', '.join(labels[k] for k in changed)}")
        else:
            for k in ("WIFI_SSID", "WIFI_PASS", "SERVER_HOST"):
                text = write_define(text, k, new[k])
            text = re.sub(r"^(#define\s+SERVER_PORT\s+)\d+", lambda mm: mm.group(1) + str(PORT), text, count=1, flags=re.M)
            with open(CONFIG, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            print(LINE)
            print(f" config.h updated ({', '.join(labels[k] for k in changed)})")
            print(" These are only the FIRST-BOOT defaults. A device that already runs firmware 2.x keeps its")
            print(" own WiFi list: add this network in the dashboard (Settings > Network, or Configure over USB)")
            print(" - no upload needed. Upload only for a brand-new board (FIRMWARE_TOOLS.bat, menu 2).")
            print(LINE)

    print()
    firewall(args.dry_run)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(1)
