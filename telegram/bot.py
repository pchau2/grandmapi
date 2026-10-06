#!/usr/bin/env python3
import os
import sys
import json
import time
import threading
import subprocess
import urllib.request
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from firestick import adb_manager, discovery

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

connected_ip = None
_wifi_was_connected = True

HEARTBEAT_INTERVAL = 86400   # 24 hours
WIFI_CHECK_INTERVAL = 60     # 1 minute
FS_CHECK_INTERVAL = 120      # 2 minutes


# ------------------------------------------------------------------ API helpers

def _api_get(method, params=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=35) as resp:
            return json.loads(resp.read())
    except Exception:
        return {"ok": False}


def _api_post(method, **params):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception:
        return {"ok": False}


def send(text):
    _api_post("sendMessage", chat_id=CHAT_ID, text=text, parse_mode="Markdown")


def send_photo(path, caption=""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    boundary = "grandmapiboundary"
    with open(path, "rb") as f:
        photo_data = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{CHAT_ID}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="caption"\r\n\r\n'
        f"{caption}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="screen.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + photo_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception:
        return {"ok": False}


# ------------------------------------------------------------------ monitoring

def _is_wifi_connected():
    try:
        result = subprocess.run(
            ["iwgetid", "-r"], capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() != ""
    except Exception:
        return False


def _heartbeat_loop():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        wifi = _is_wifi_connected()
        fs = "Connected" if connected_ip else "Not connected"
        send(
            f"grandmapi heartbeat\n"
            f"WiFi: {'Connected' if wifi else 'Disconnected'}\n"
            f"Fire Stick: {fs}"
        )


def _wifi_monitor_loop():
    global _wifi_was_connected
    while True:
        time.sleep(WIFI_CHECK_INTERVAL)
        connected = _is_wifi_connected()
        if not connected and _wifi_was_connected:
            _wifi_was_connected = False
            # Can't send Telegram if WiFi is down — just log it
            print("[monitor] WiFi connection lost.")
        elif connected and not _wifi_was_connected:
            _wifi_was_connected = True
            send("WiFi reconnected.")
            print("[monitor] WiFi restored.")


def _firestick_monitor_loop():
    global connected_ip
    last_state = None

    while True:
        time.sleep(FS_CHECK_INTERVAL)
        if connected_ip:
            ok, _, _ = adb_manager._adb(connected_ip, "shell", "echo", "ok", timeout=5)
            if not ok:
                send(f"Fire Stick disconnected ({connected_ip}).")
                print(f"[monitor] Fire Stick {connected_ip} disconnected.")
                connected_ip = None
                last_state = "disconnected"
        else:
            if last_state != "scanning":
                last_state = "scanning"
            devices = discovery.scan()
            for ip in devices:
                if adb_manager.connect(ip):
                    connected_ip = ip
                    name = adb_manager.get_device_name(ip)
                    adb_manager.disable_voice(ip)
                    send(f"Fire Stick reconnected: {name} ({ip})")
                    print(f"[monitor] Fire Stick {ip} reconnected.")
                    last_state = "connected"
                    break


def start_monitors():
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    threading.Thread(target=_wifi_monitor_loop, daemon=True).start()
    threading.Thread(target=_firestick_monitor_loop, daemon=True).start()


# ------------------------------------------------------------------ Fire Stick

def ensure_connected():
    global connected_ip
    if connected_ip:
        ok, _, _ = adb_manager._adb(connected_ip, "shell", "echo", "ok", timeout=5)
        if ok:
            return True
        connected_ip = None

    send("Scanning for Fire Stick...")
    devices = discovery.scan()
    if not devices:
        send("No Fire Stick found on the network.")
        return False

    for ip in devices:
        if adb_manager.connect(ip):
            connected_ip = ip
            name = adb_manager.get_device_name(ip)
            adb_manager.disable_voice(ip)
            send(f"Connected to {name} ({ip})")
            return True

    send("Found a device but couldn't connect. Make sure ADB is enabled on the Fire Stick.")
    return False


# ------------------------------------------------------------------ commands

HELP_TEXT = (
    "*grandmapi commands*\n\n"
    "/screenshot - Take a screenshot of the Fire Stick\n"
    "/youtube - Open YouTube\n"
    "/ytlock - Lock Fire Stick to YouTube only\n"
    "/ytunlock - Restore normal Fire Stick access\n"
    "/disablemic - Disable voice search (mic button = help alert only)\n"
    "/enablemic - Re-enable voice search\n"
    "/reboot - Reboot the Fire Stick\n"
    "/status - Check connection status\n"
    "/help - Show this message"
)


def handle_command(text):
    global connected_ip
    cmd = text.strip().lower().split()[0]

    if cmd == "/help":
        send(HELP_TEXT)

    elif cmd == "/status":
        wifi = _is_wifi_connected()
        if connected_ip:
            ok, _, _ = adb_manager._adb(connected_ip, "shell", "echo", "ok", timeout=5)
            if ok:
                name = adb_manager.get_device_name(connected_ip)
                fs_status = f"Connected to {name} ({connected_ip})"
            else:
                connected_ip = None
                fs_status = "Disconnected"
        else:
            fs_status = "Not connected"
        send(
            f"*grandmapi status*\n"
            f"WiFi: {'Connected' if wifi else 'Disconnected'}\n"
            f"Fire Stick: {fs_status}"
        )

    elif cmd == "/screenshot":
        if not ensure_connected():
            return
        send("Taking screenshot...")
        path = adb_manager.screenshot(connected_ip)
        if path:
            send_photo(path, "Fire Stick screen")
        else:
            send("Screenshot failed.")

    elif cmd == "/youtube":
        if not ensure_connected():
            return
        adb_manager.open_youtube(connected_ip)
        send("YouTube opened.")

    elif cmd == "/ytlock":
        if not ensure_connected():
            return
        adb_manager.lock_to_youtube(connected_ip)
        send("Fire Stick locked to YouTube only.")

    elif cmd == "/ytunlock":
        if not ensure_connected():
            return
        adb_manager.unlock(connected_ip)
        send("Fire Stick unlocked.")

    elif cmd == "/disablemic":
        if not ensure_connected():
            return
        adb_manager.disable_voice(connected_ip)
        send("Mic button disabled. It will now only trigger the help alert.")

    elif cmd == "/enablemic":
        if not ensure_connected():
            return
        adb_manager.enable_voice(connected_ip)
        send("Mic button re-enabled. Voice search restored.")

    elif cmd == "/reboot":
        if not ensure_connected():
            return
        send("Rebooting Fire Stick...")
        adb_manager._adb(connected_ip, "shell", "reboot")
        connected_ip = None

    else:
        send(f"Unknown command: `{cmd}`\nType /help for available commands.")


# ------------------------------------------------------------------ main loop

def run():
    if not BOT_TOKEN or not CHAT_ID:
        print("Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID not set in /etc/grandmapi.env")
        sys.exit(1)

    print("grandmapi bot started.")
    send(
        "grandmapi is online.\n"
        f"WiFi: {'Connected' if _is_wifi_connected() else 'Disconnected'}\n"
        "Type /help for available commands."
    )

    start_monitors()

    offset = 0
    while True:
        try:
            result = _api_get("getUpdates", {
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["message"]
            })
            if result.get("ok"):
                for update in result.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "")

                    if chat_id != str(CHAT_ID):
                        continue

                    if text.startswith("/"):
                        handle_command(text)
        except Exception as e:
            print(f"Error in bot loop: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run()
