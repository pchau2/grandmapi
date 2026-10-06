#!/usr/bin/env python3
import os
import sys
import json
import time
import datetime
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
_ytlock_active = True
_callback_lock = threading.Lock()

HEARTBEAT_HOUR = 9           # send daily check-in at 9 AM
WIFI_CHECK_INTERVAL = 60     # 1 minute
FS_CHECK_INTERVAL = 120      # 2 minutes
YTLOCK_CHECK_INTERVAL = 10   # 10 seconds

REMOTE_KEYBOARD = json.dumps({
    "inline_keyboard": [
        [{"text": "▲", "callback_data": "key_up"}],
        [
            {"text": "◀", "callback_data": "key_left"},
            {"text": "✅ OK", "callback_data": "key_select"},
            {"text": "▶", "callback_data": "key_right"}
        ],
        [{"text": "▼", "callback_data": "key_down"}],
        [
            {"text": "🏠 Home", "callback_data": "key_home"},
            {"text": "↩ Back", "callback_data": "key_back"}
        ],
        [
            {"text": "⏮", "callback_data": "key_rew"},
            {"text": "⏯", "callback_data": "key_play"},
            {"text": "⏭", "callback_data": "key_fwd"}
        ],
        [
            {"text": "🔊+", "callback_data": "key_vol_up"},
            {"text": "🔊–", "callback_data": "key_vol_down"}
        ],
        [
            {"text": "▶️ YouTube", "callback_data": "action_youtube"},
            {"text": "📺 History", "callback_data": "action_history"}
        ],
        [
            {"text": "🔒 YT Lock", "callback_data": "action_ytlock"},
            {"text": "🔓 YT Unlock", "callback_data": "action_ytunlock"}
        ],
        [{"text": "📸 Refresh screenshot", "callback_data": "refresh"}],
        [{"text": "✅ Done — grandma is all set", "callback_data": "done"}]
    ]
})

KEY_MAP = {
    "key_up":      adb_manager.KEY_UP,
    "key_down":    adb_manager.KEY_DOWN,
    "key_left":    adb_manager.KEY_LEFT,
    "key_right":   adb_manager.KEY_RIGHT,
    "key_select":  adb_manager.KEY_SELECT,
    "key_home":    adb_manager.KEY_HOME,
    "key_back":    adb_manager.KEY_BACK,
    "key_play":    adb_manager.KEY_PLAY_PAUSE,
    "key_vol_up":  adb_manager.KEY_VOLUME_UP,
    "key_vol_down": adb_manager.KEY_VOLUME_DOWN,
    "key_rew":     89,   # KEYCODE_MEDIA_REWIND
    "key_fwd":     90,   # KEYCODE_MEDIA_FAST_FORWARD
}


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
    result = _api_post("sendMessage", chat_id=CHAT_ID, text=text, parse_mode="Markdown")
    return result.get("result", {}).get("message_id")


def pin_message(message_id):
    if message_id:
        _api_post("pinChatMessage", chat_id=CHAT_ID, message_id=message_id,
                  disable_notification=True)


def answer_callback(callback_query_id, text=""):
    _api_post("answerCallbackQuery", callback_query_id=callback_query_id, text=text)


def edit_photo_message(message_id, photo_path, caption):
    """Replace the photo in an existing message with a fresh screenshot."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageMedia"
    boundary = "grandmapiboundary"
    media = json.dumps({
        "type": "photo",
        "media": "attach://photo",
        "caption": caption,
        "parse_mode": "Markdown"
    })
    with open(photo_path, "rb") as f:
        photo_data = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{CHAT_ID}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="message_id"\r\n\r\n'
        f"{message_id}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="media"\r\n\r\n'
        f"{media}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="reply_markup"\r\n\r\n'
        f"{REMOTE_KEYBOARD}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="screen.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + photo_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"[callback] edit_photo_message error: {e}")
        return {"ok": False}


def handle_callback(callback_query):
    global _ytlock_active
    query_id = callback_query["id"]
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    message_id = message.get("message_id")
    is_photo = "photo" in message
    caption = message.get("caption") or message.get("text", "🆘 *GRANDMA NEEDS HELP!*")

    if not connected_ip:
        answer_callback(query_id, "⚠️ Fire Stick not connected")
        return

    # Done button — delete the alert message immediately, no lock needed
    if data == "done":
        answer_callback(query_id, "👍 Marked as resolved")
        if message_id:
            _api_post("deleteMessage", chat_id=CHAT_ID, message_id=message_id)
        return

    # Reject if already processing a button press
    if not _callback_lock.acquire(blocking=False):
        answer_callback(query_id, "⏳ Still processing, please wait...")
        return

    try:
        answer_callback(query_id)

        if data in KEY_MAP:
            adb_manager.send_key(connected_ip, KEY_MAP[data])
            time.sleep(0.5)

        elif data == "action_youtube":
            adb_manager.open_youtube(connected_ip)
            time.sleep(3)

        elif data == "action_history":
            adb_manager.open_youtube_history(connected_ip)

        elif data == "action_ytlock":
            _ytlock_active = True
            adb_manager.lock_to_youtube(connected_ip)
            time.sleep(3)

        elif data == "action_ytunlock":
            _ytlock_active = False
            time.sleep(1)

        # Take a fresh screenshot and update the message in-place
        path = adb_manager.screenshot(connected_ip)
        if path and message_id:
            if is_photo:
                edit_photo_message(message_id, path, caption)
            else:
                send_photo(path, caption)
    finally:
        _callback_lock.release()


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
            result = json.loads(resp.read())
            return result.get("result", {}).get("message_id")
    except Exception:
        return None


# ------------------------------------------------------------------ monitoring

def _service_status(name):
    try:
        r = subprocess.run(["systemctl", "is-active", name],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def _health_report():
    services = [
        ("grandmapi-telegram", "Bot"),
        ("grandmapi-monitor", "Fire Stick monitor"),
        ("grandmapi-wifi", "WiFi GUI"),
        ("tailscaled", "Tailscale"),
        ("ssh", "SSH"),
    ]
    lines = []
    for svc, label in services:
        st = _service_status(svc)
        icon = "✅" if st == "active" else "❌"
        lines.append(f"{icon} {label}")
    svc_block = "\n".join(lines)

    # Tailscale IP
    try:
        ts = subprocess.run(["tailscale", "ip", "-4"],
                            capture_output=True, text=True, timeout=5)
        ts_ip = ts.stdout.strip() if ts.returncode == 0 else "unavailable"
    except Exception:
        ts_ip = "unavailable"

    # Disk usage
    try:
        df = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        parts = df.stdout.strip().split("\n")[-1].split()
        disk = f"{parts[2]} used of {parts[1]} ({parts[4]})"
    except Exception:
        disk = "unavailable"

    # Memory
    try:
        mem = subprocess.run(["free", "-h"], capture_output=True, text=True, timeout=5)
        parts = mem.stdout.strip().split("\n")[1].split()
        memory = f"{parts[2]} used of {parts[1]}"
    except Exception:
        memory = "unavailable"

    return svc_block, ts_ip, disk, memory


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
        # Sleep until next 9 AM
        now = datetime.datetime.now()
        next_run = now.replace(hour=HEARTBEAT_HOUR, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += datetime.timedelta(days=1)
        time.sleep((next_run - now).total_seconds())

        wifi = _is_wifi_connected()
        wifi_icon = "✅" if wifi else "❌"
        fs_icon = "✅" if connected_ip else "❌"
        fs = "Connected" if connected_ip else "Not connected"
        lock_line = "\n🔒 YouTube lock: *Active*" if _ytlock_active else ""
        svc_block, ts_ip, disk, memory = _health_report()
        msg_id = send(
            f"💓 *grandmapi daily check-in*\n\n"
            f"{wifi_icon} WiFi: {'Connected' if wifi else 'Disconnected'}\n"
            f"{fs_icon} Fire Stick: {fs}"
            f"{lock_line}\n\n"
            f"*Services:*\n{svc_block}\n\n"
            f"🌐 Tailscale: `{ts_ip}`\n"
            f"💾 Disk: {disk}\n"
            f"🧠 RAM: {memory}"
        )
        pin_message(msg_id)


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
            send("✅ *WiFi reconnected*\ngrandmapi is back online.")
            print("[monitor] WiFi restored.")


def _firestick_monitor_loop():
    global connected_ip
    was_connected = False

    while True:
        if connected_ip:
            ok, _, _ = adb_manager._adb(connected_ip, "shell", "echo", "ok", timeout=5)
            if not ok:
                send(
                    f"⚠️ *Fire Stick disconnected*\n\n"
                    f"`{connected_ip}` is no longer reachable.\n"
                    f"Will scan and reconnect automatically."
                )
                print(f"[monitor] Fire Stick {connected_ip} disconnected.")
                connected_ip = None
                was_connected = True
        else:
            devices = discovery.scan()
            for ip in devices:
                if adb_manager.connect(ip):
                    connected_ip = ip
                    name = adb_manager.get_device_name(ip)
                    adb_manager.disable_voice(ip)
                    adb_manager.lock_to_youtube(ip)
                    if was_connected:
                        send(f"✅ *Fire Stick reconnected*\n\n*{name}* (`{ip}`)")
                    print(f"[monitor] Fire Stick {ip} connected.")
                    was_connected = False
                    break
        time.sleep(FS_CHECK_INTERVAL)


def _ytlock_monitor_loop():
    while True:
        time.sleep(YTLOCK_CHECK_INTERVAL)
        if _ytlock_active and connected_ip:
            try:
                if not adb_manager.is_youtube_foreground(connected_ip):
                    adb_manager.open_youtube(connected_ip)
            except Exception:
                pass


def start_monitors():
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    threading.Thread(target=_wifi_monitor_loop, daemon=True).start()
    threading.Thread(target=_firestick_monitor_loop, daemon=True).start()
    threading.Thread(target=_ytlock_monitor_loop, daemon=True).start()


# ------------------------------------------------------------------ Fire Stick

def ensure_connected():
    global connected_ip
    if connected_ip:
        ok, _, _ = adb_manager._adb(connected_ip, "shell", "echo", "ok", timeout=5)
        if ok:
            return True
        connected_ip = None

    send("🔍 Scanning for Fire Stick...")
    devices = discovery.scan()
    if not devices:
        send("❌ No Fire Stick found on the network.")
        return False

    for ip in devices:
        if adb_manager.connect(ip):
            connected_ip = ip
            name = adb_manager.get_device_name(ip)
            adb_manager.disable_voice(ip)
            adb_manager.lock_to_youtube(ip)
            send(f"✅ Connected to *{name}* (`{ip}`)")
            return True

    send("❌ Found a device but couldn't connect. Make sure ADB debugging is enabled on the Fire Stick.")
    return False


# ------------------------------------------------------------------ commands

HELP_TEXT = (
    "*grandmapi* 🏠\n\n"
    "*📺 Fire Stick*\n"
    "/youtube — Open YouTube\n"
    "/history — Resume most recent YouTube video\n"
    "/ytlock — Lock to YouTube only\n"
    "/ytunlock — Restore normal access\n"
    "/screenshot — Capture the screen\n"
    "/reboot — Reboot the Fire Stick\n\n"
    "*ℹ️ Info*\n"
    "/status — Connection status\n"
    "/heartbeat — Send daily check-in now\n"
    "/help — Show this message"
)


def handle_command(text):
    global connected_ip, _ytlock_active
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
        wifi_icon = "✅" if wifi else "❌"
        fs_icon = "✅" if connected_ip else "❌"
        lock_line = "\n🔒 YouTube lock: *Active*" if _ytlock_active else ""
        msg_id = send(
            f"*grandmapi status* 📡\n\n"
            f"{wifi_icon} WiFi: {'Connected' if wifi else 'Disconnected'}\n"
            f"{fs_icon} Fire Stick: {fs_status}"
            f"{lock_line}"
        )
        pin_message(msg_id)

    elif cmd == "/heartbeat":
        wifi = _is_wifi_connected()
        wifi_icon = "✅" if wifi else "❌"
        fs_icon = "✅" if connected_ip else "❌"
        fs = f"Connected to {adb_manager.get_device_name(connected_ip)} (`{connected_ip}`)" if connected_ip else "Not connected"
        lock_line = "\n🔒 YouTube lock: *Active*" if _ytlock_active else ""
        svc_block, ts_ip, disk, memory = _health_report()
        msg_id = send(
            f"💓 *grandmapi daily check-in*\n\n"
            f"{wifi_icon} WiFi: {'Connected' if wifi else 'Disconnected'}\n"
            f"{fs_icon} Fire Stick: {fs}"
            f"{lock_line}\n\n"
            f"*Services:*\n{svc_block}\n\n"
            f"🌐 Tailscale: `{ts_ip}`\n"
            f"💾 Disk: {disk}\n"
            f"🧠 RAM: {memory}"
        )
        pin_message(msg_id)

    elif cmd == "/screenshot":
        if not ensure_connected():
            return
        send("📸 Taking screenshot...")
        path = adb_manager.screenshot(connected_ip)
        if path:
            send_photo(path, "Fire Stick screen")
        else:
            send("❌ Screenshot failed. The screen may be protected by DRM.")

    elif cmd == "/youtube":
        if not ensure_connected():
            return
        adb_manager.open_youtube(connected_ip)
        send("▶️ YouTube opened.")

    elif cmd == "/history":
        if not ensure_connected():
            return
        send("📺 Opening YouTube history...")
        adb_manager.open_youtube_history(connected_ip)

    elif cmd == "/ytlock":
        if not ensure_connected():
            return
        _ytlock_active = True
        adb_manager.lock_to_youtube(connected_ip)
        send("🔒 Fire Stick locked to YouTube. It will relaunch automatically if closed.")

    elif cmd == "/ytunlock":
        if not ensure_connected():
            return
        _ytlock_active = False
        adb_manager.unlock(connected_ip)
        send("🔓 Fire Stick unlocked — normal access restored.")

    elif cmd == "/reboot":
        if not ensure_connected():
            return
        send("🔄 *Rebooting Fire Stick...*\nIt will reconnect automatically in about 30 seconds.")
        adb_manager._adb(connected_ip, "shell", "reboot")
        connected_ip = None

    else:
        send(f"❓ Unknown command: `{cmd}`\n\nType /help to see all available commands.")


# ------------------------------------------------------------------ main loop

def run():
    if not BOT_TOKEN or not CHAT_ID:
        print("Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID not set in /etc/grandmapi.env")
        sys.exit(1)

    global connected_ip
    print("grandmapi bot started.")
    wifi = _is_wifi_connected()

    # Try to connect to Fire Stick before sending startup message
    devices = discovery.scan()
    for ip in devices:
        if adb_manager.connect(ip):
            connected_ip = ip
            adb_manager.disable_voice(ip)
            adb_manager.lock_to_youtube(ip)
            print(f"[startup] Connected to Fire Stick at {ip}")
            break

    fs_icon = "✅" if connected_ip else "❌"
    fs_name = adb_manager.get_device_name(connected_ip) if connected_ip else "Not found"
    fs_line = f"{fs_icon} Fire Stick: {fs_name} (`{connected_ip}`)" if connected_ip else f"{fs_icon} Fire Stick: Not found"

    msg_id = send(
        f"✅ *grandmapi is online* 🏠\n\n"
        f"{'✅' if wifi else '❌'} WiFi: {'Connected' if wifi else 'Disconnected'}\n"
        f"{fs_line}\n\n"
        f"Type /help for available commands."
    )
    pin_message(msg_id)

    start_monitors()

    offset = 0
    while True:
        try:
            result = _api_get("getUpdates", {
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"]
            })
            if result.get("ok"):
                for update in result.get("result", []):
                    offset = update["update_id"] + 1

                    if "callback_query" in update:
                        cq = update["callback_query"]
                        if str(cq.get("from", {}).get("id", "")) == str(CHAT_ID):
                            threading.Thread(
                                target=handle_callback, args=(cq,), daemon=True
                            ).start()

                    elif "message" in update:
                        msg = update["message"]
                        chat_id = str(msg.get("chat", {}).get("id", ""))
                        text = msg.get("text", "")
                        if chat_id == str(CHAT_ID) and text.startswith("/"):
                            handle_command(text)
        except Exception as e:
            print(f"Error in bot loop: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run()
