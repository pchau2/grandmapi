#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

POLL_INTERVAL = 30   # seconds between reconnect attempts

connected_ip = None
on_help_triggered = None  # callback set by caller


def _log(msg):
    print(msg, flush=True)


def _watch_events(ip):
    """Stream input events from Fire Stick and detect mic button press."""
    _log(f"[button_monitor] Starting getevent stream on {ip}...")
    proc = subprocess.Popen(
        ["adb", "-s", f"{ip}:5555", "shell", "getevent", "-l"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    last_mic_down = 0

    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue

        # Debug: log mic-related lines
        if any(k in line for k in ("KEY_SEARCH", "KEY_VOICE", "KEY_ASSIST", "EV_KEY")):
            _log(f"[button_monitor] event: {repr(line)}")

        # Mic button sends KEY_SEARCH on Fire Stick remotes
        if "KEY_SEARCH" in line or "KEY_VOICE_ASSIST" in line or "KEY_ASSIST" in line:
            if "DOWN" in line or " 1 " in line:
                last_mic_down = time.time()
                _log("[button_monitor] Mic DOWN detected")
            elif ("UP" in line or " 0 " in line) and last_mic_down:
                _log("[button_monitor] Mic UP detected — triggering help!")
                if on_help_triggered:
                    threading.Thread(target=on_help_triggered, daemon=True).start()
                last_mic_down = 0

    proc.wait()
    err = proc.stderr.read()
    if err:
        _log(f"[button_monitor] getevent stderr: {err.strip()}")
    _log(f"[button_monitor] getevent stream ended for {ip}")


def _connect_and_watch(ip):
    global connected_ip
    from firestick import adb_manager
    _log(f"[button_monitor] Connecting to {ip}...")
    if adb_manager.connect(ip):
        connected_ip = ip
        _log(f"[button_monitor] Connected to {ip}, disabling voice...")
        adb_manager.disable_voice(ip)
        _log(f"[button_monitor] Watching for mic button on {ip}")
        _watch_events(ip)
        _log(f"[button_monitor] Lost connection to {ip}")
        connected_ip = None
    else:
        _log(f"[button_monitor] Could not connect to {ip}")


def _scan_and_connect():
    from firestick import discovery
    _log("[button_monitor] Scanning for Fire Stick...")
    try:
        devices = discovery.scan()
    except Exception as e:
        _log(f"[button_monitor] Scan error: {e}")
        return
    _log(f"[button_monitor] Found devices: {devices}")
    for ip in devices:
        try:
            _connect_and_watch(ip)
        except Exception as e:
            _log(f"[button_monitor] Error connecting to {ip}: {e}")
        break


def run(help_callback):
    """
    Start monitoring. help_callback is called when mic button is pressed.
    Blocks forever — run in a thread or as a standalone process.
    """
    global on_help_triggered
    on_help_triggered = help_callback

    _log("[button_monitor] Starting main loop...")
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
    else:
        _log(f"[button_monitor] WARNING: {env_file} not found")

    try:
        from telegram.notify import send
        from firestick import adb_manager
    except Exception as e:
        _log(f"[button_monitor] FATAL: could not import modules: {e}")
        sys.exit(1)

    def on_help():
        _log("[button_monitor] Mic button pressed — restarting YouTube and sending help alert!")
        try:
            if connected_ip:
                adb_manager.restart_youtube(connected_ip)
        except Exception as e:
            _log(f"[button_monitor] Error restarting YouTube: {e}")
        try:
            send(connected_ip)
        except Exception as e:
            _log(f"[button_monitor] Error sending alert: {e}")

    run(on_help)
