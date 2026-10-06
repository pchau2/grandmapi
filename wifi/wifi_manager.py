import subprocess
import re


def scan_networks():
    try:
        result = subprocess.run(
            ["sudo", "iwlist", "wlan0", "scan"],
            capture_output=True, text=True, timeout=15
        )
        ssids = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("ESSID:"):
                ssid = line[7:].strip('"')
                if ssid and ssid not in ssids:
                    ssids.append(ssid)
        return ssids
    except Exception:
        return []


def is_connected():
    try:
        result = subprocess.run(
            ["iwgetid", "-r"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() != ""
    except Exception:
        return False


def current_ssid():
    try:
        result = subprocess.run(
            ["iwgetid", "-r"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


def connect(ssid, password):
    try:
        subprocess.run(["sudo", "killall", "wpa_supplicant"], capture_output=True)

        config = f'ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev\nupdate_config=1\ncountry=US\n\n'
        result = subprocess.run(
            ["wpa_passphrase", ssid, password],
            capture_output=True, text=True
        )
        config += result.stdout

        with open("/tmp/wpa_gui.conf", "w") as f:
            f.write(config)

        subprocess.Popen(
            ["sudo", "wpa_supplicant", "-B", "-i", "wlan0", "-c", "/tmp/wpa_gui.conf"],
        )

        import time
        time.sleep(5)

        subprocess.Popen(["sudo", "dhcpcd", "wlan0"])
        time.sleep(5)

        return is_connected()
    except Exception as e:
        return False
