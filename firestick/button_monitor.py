#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from firestick import adb_manager, discovery

POLL_INTERVAL = 30   # seconds between reconnect attempts
SCAN_INTERVAL = 60   # seconds between rediscovery scans

connected_ip = None
on_help_triggered = None  # callback set by caller


def _watch_events(ip):
    """Stream input events from Fire Stick and detect mic button press."""
    proc = subprocess.Popen(
        ["adb", "-s", f"{ip}:5555", "shell", "getevent", "-l"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True
    )

    last_mic_down = 0

    try:
        for line in proc.stdout:
            line = line.strip()
            # Mic button generates KEY_SEARCH or KEY_VOICE_ASSIST press
            if ("KEY_SEARCH" in line or "KEY_VOICE_ASSIST" in line or "KEY_ASSIST" in line):
                if "DOWN" in line or "0001" in line:
                    last_mic_down = time.time()
                elif ("UP" in line or "0000" in line) and last_mic_down:
                    held = time.time() - last_mic_down
                    # Any press of mic button triggers help
                    if on_help_triggered:
                        threading.Thread(target=on_help_triggered, daemon=True).start()
                    last_mic_down = 0
    except Exception:
        pass
    finally:
        proc.kill()


def _connect_and_watch(ip):
    global connected_ip
    if adb_manager.connect(ip):
        connected_ip = ip
        adb_manager.disable_voice(ip)
        print(f"[button_monitor] Connected to {ip}, voice disabled, watching for mic button...")
        _watch_events(ip)
        print(f"[button_monitor] Lost connection to {ip}")
        connected_ip = None


def _scan_and_connect():
    global connected_ip
    print("[button_monitor] Scanning for Fire Stick...")
    devices = discovery.scan()
    for ip in devices:
        _connect_and_watch(ip)
        break


def run(help_callback):
    """
    Start monitoring. help_callback is called when mic button is pressed.
    Blocks forever — run in a thread or as a standalone process.
    """
    global on_help_triggered
    on_help_triggered = help_callback

    while True:
        if not connected_ip:
            _scan_and_connect()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    # Load env credentials for Telegram notify
    env_file = "/etc/grandmapi.env"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from telegram.notify import send

    def on_help():
        print("[button_monitor] Mic button pressed — sending help alert!")
        send()

    run(on_help)
