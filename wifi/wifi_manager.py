import subprocess
import json
import os
import time

SAVED_NETWORKS_PATH = "/etc/grandmapi_networks.json"


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


def load_saved_networks():
    try:
        if os.path.exists(SAVED_NETWORKS_PATH):
            with open(SAVED_NETWORKS_PATH) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_network(ssid, password):
    networks = load_saved_networks()
    networks[ssid] = password
    try:
        tmp = SAVED_NETWORKS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(networks, f)
        os.replace(tmp, SAVED_NETWORKS_PATH)
    except Exception:
        # Fall back to writing directly if sudo needed
        subprocess.run(
            ["sudo", "tee", SAVED_NETWORKS_PATH],
            input=json.dumps(networks),
            capture_output=True, text=True
        )


def _build_wpa_config(networks_dict):
    config = "ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev\nupdate_config=1\ncountry=US\n\n"
    for ssid, password in networks_dict.items():
        result = subprocess.run(
            ["wpa_passphrase", ssid, password],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            config += result.stdout + "\n"
    return config


def _apply_wpa_config(config):
    with open("/tmp/wpa_gui.conf", "w") as f:
        f.write(config)
    subprocess.run(["sudo", "killall", "wpa_supplicant"], capture_output=True)
    subprocess.Popen(
        ["sudo", "wpa_supplicant", "-B", "-i", "wlan0", "-c", "/tmp/wpa_gui.conf"]
    )
    time.sleep(5)
    subprocess.Popen(["sudo", "dhcpcd", "wlan0"])
    time.sleep(5)


def auto_connect():
    """Try all saved networks. Returns True if connected."""
    networks = load_saved_networks()
    if not networks:
        return False
    config = _build_wpa_config(networks)
    _apply_wpa_config(config)
    return is_connected()


def connect(ssid, password):
    try:
        # Build config with all saved networks + this new one
        networks = load_saved_networks()
        networks[ssid] = password
        config = _build_wpa_config(networks)
        _apply_wpa_config(config)

        if is_connected():
            save_network(ssid, password)
            return True
        return False
    except Exception:
        return False
