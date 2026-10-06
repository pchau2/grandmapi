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
from telegram import notify

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

connected_ip = None
_wifi_was_connected = True
_ytlock_active = True
_callback_lock = threading.Lock()
_startup_msg_id = None
_session_message_ids = []  # messages to bulk-delete when Done is pressed
_stream_proc = None        # live-stream server subprocess, when running
STREAM_PORT = 8080

HEARTBEAT_HOUR = 9           # send daily check-in at 9 AM
WIFI_CHECK_INTERVAL = 60     # 1 minute
FS_CHECK_INTERVAL = 120      # 2 minutes
YTLOCK_CHECK_INTERVAL = 10   # 10 seconds
HEALTH_CHECK_INTERVAL = 1800 # 30 minutes

# Health alert thresholds — alert on crossing, recover below the hysteresis gap
TEMP_WARN_C = 75             # alert above this
TEMP_CLEAR_C = 68            # consider recovered below this
DISK_WARN_PCT = 90           # alert at/above this % used

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

# Seconds to wait after key press before taking screenshot.
# Transition keys (home/back) load a full new screen; nav keys update in place.
# Nav keys (arrows) default to 0.25s for a snappy, near-live feel.
KEY_DELAYS = {
    "key_home":   1.5,
    "key_back":   1.0,
    "key_select": 0.9,
    "key_rew":    0.9,
    "key_fwd":    0.9,
    "key_play":   0.4,
}
NAV_DELAY = 0.25  # default for arrow keys and anything not listed above


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


def _track(message_id):
    """Remember a transient message so Done can clean it up later."""
    if message_id:
        _session_message_ids.append(message_id)
        # Cap the list so it can't grow unbounded between Done presses
        if len(_session_message_ids) > 50:
            del _session_message_ids[:-50]
    return message_id


def send(text):
    result = _api_post("sendMessage", chat_id=CHAT_ID, text=text, parse_mode="Markdown")
    return _track(result.get("result", {}).get("message_id"))


def pin_message(message_id):
    if message_id:
        # A pinned message (status board, heartbeat) is permanent — never clean it up
        if message_id in _session_message_ids:
            _session_message_ids.remove(message_id)
        _api_post("pinChatMessage", chat_id=CHAT_ID, message_id=message_id,
                  disable_notification=True)


def edit_message(message_id, text):
    if message_id:
        _api_post("editMessageText", chat_id=CHAT_ID, message_id=message_id,
                  text=text, parse_mode="Markdown")


def _read_alert_msg_id():
    try:
        with open(notify.ALERT_MSG_ID_PATH) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _clear_alert_msg_id():
    try:
        os.remove(notify.ALERT_MSG_ID_PATH)
    except Exception:
        pass


def _alert_caption(status=None):
    """Build alert caption, optionally with a status line under the header."""
    if status:
        return notify.ALERT_TEXT.replace(
            "🆘 *GRANDMA NEEDS HELP!*",
            f"🆘 *GRANDMA NEEDS HELP!*\n{status}"
        )
    return notify.ALERT_TEXT


def _update_alert_caption(message_id, caption):
    """Edit just the caption of the alert photo message."""
    if message_id:
        _api_post("editMessageCaption", chat_id=CHAT_ID, message_id=message_id,
                  caption=caption, parse_mode="Markdown", reply_markup=REMOTE_KEYBOARD)


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
        f"Content-Type: {'image/jpeg' if photo_path.lower().endswith('.jpg') else 'image/png'}\r\n\r\n"
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

    # Done button — delete this message and all tracked session messages
    if data == "done":
        answer_callback(query_id, "👍 Marked as resolved")
        _clear_alert_msg_id()
        to_delete = list(_session_message_ids)
        _session_message_ids.clear()
        if message_id:
            to_delete.append(message_id)
        for mid in to_delete:
            if mid:
                _api_post("deleteMessage", chat_id=CHAT_ID, message_id=mid)
        return

    # Reject if already processing a button press
    if not _callback_lock.acquire(blocking=False):
        answer_callback(query_id, "⏳ Still processing, please wait...")
        return

    try:
        answer_callback(query_id)

        if data in KEY_MAP:
            adb_manager.send_key(connected_ip, KEY_MAP[data])
            time.sleep(KEY_DELAYS.get(data, NAV_DELAY))

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


def send_photo(path, caption="", reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    boundary = "grandmapiboundary"
    with open(path, "rb") as f:
        photo_data = f.read()
    parts = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{CHAT_ID}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="caption"\r\n\r\n'
        f"{caption}\r\n"
    )
    if reply_markup:
        parts += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="reply_markup"\r\n\r\n'
            f"{reply_markup}\r\n"
        )
    img_type = "image/jpeg" if path.lower().endswith(".jpg") else "image/png"
    parts += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="screen.png"\r\n'
        f"Content-Type: {img_type}\r\n\r\n"
    )
    body = parts.encode() + photo_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return _track(result.get("result", {}).get("message_id"))
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
        ("grandmapi-stream", "Live stream"),
        ("NetworkManager", "WiFi"),
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
    ts_ip = _tailscale_ip() or "unavailable"

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


def _cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            c = int(f.read().strip()) / 1000
        icon = "🔥" if c > 70 else "🌡"
        return f"{icon} {c:.1f}°C"
    except Exception:
        return "unavailable"


def _cpu_load():
    try:
        with open("/proc/loadavg") as f:
            p = f.read().split()
        return f"{p[0]} / {p[1]} / {p[2]} (1m / 5m / 15m)"
    except Exception:
        return "unavailable"


def _uptime():
    try:
        r = subprocess.run(["uptime", "-p"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return "unavailable"


def _wifi_signal():
    try:
        r = subprocess.run(["iwconfig", "wlan0"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.split("\n"):
            if "Signal level" in line:
                part = line.strip().split("Signal level=")[-1].split()[0]
                return f"📶 {part} dBm"
        return "📶 unavailable"
    except Exception:
        return "📶 unavailable"


def _throttle_status():
    try:
        r = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5)
        val = r.stdout.strip()
        return "✅ None" if "0x0" in val else f"⚠️ {val}"
    except Exception:
        return "unavailable"


def _cpu_temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000
    except Exception:
        return None


def _disk_percent():
    try:
        df = subprocess.run(["df", "/"], capture_output=True, text=True, timeout=5)
        return int(df.stdout.strip().split("\n")[-1].split()[4].rstrip("%"))
    except Exception:
        return None


def _is_throttled_now():
    """Return True only if currently throttled (bit 2), not just 'has throttled before'."""
    try:
        r = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5)
        val = int(r.stdout.strip().split("=")[-1], 16)
        return bool(val & 0x4)  # bit 2 = currently throttled
    except Exception:
        return False


def _tailscale_ip():
    # Try the CLI at common locations (systemd PATH may not include it)
    for binary in ("tailscale", "/usr/bin/tailscale", "/usr/sbin/tailscale"):
        try:
            r = subprocess.run([binary, "ip", "-4"], capture_output=True, text=True, timeout=5)
            ip = r.stdout.strip().split("\n")[0].strip()
            if r.returncode == 0 and ip:
                return ip
        except Exception:
            continue
    # Fall back to reading the tailscale0 interface directly
    try:
        r = subprocess.run(["ip", "-4", "addr", "show", "tailscale0"],
                           capture_output=True, text=True, timeout=5)
        for line in r.stdout.split("\n"):
            line = line.strip()
            if line.startswith("inet "):
                return line.split()[1].split("/")[0]
    except Exception:
        pass
    return None


def _lan_ip():
    """The Pi's local WiFi IP (192.168.x / 10.x), excluding Tailscale and Docker."""
    try:
        r = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        for tok in r.stdout.split():
            if tok.startswith("192.168.") or tok.startswith("10."):
                return tok
            if tok.startswith("172.") and not tok.startswith("172.17."):
                return tok
    except Exception:
        pass
    return None


_health_state = {"temp": False, "disk": False, "throttle": False}


def _health_alert_loop():
    """Alert once when a hardware problem appears, once more when it clears."""
    while True:
        time.sleep(HEALTH_CHECK_INTERVAL)
        try:
            # CPU temperature (hysteresis so it doesn't flap around the threshold)
            temp = _cpu_temp_c()
            if temp is not None:
                if not _health_state["temp"] and temp >= TEMP_WARN_C:
                    _health_state["temp"] = True
                    send(f"🔥 *Pi running hot*\nCPU is {temp:.1f}°C. Check ventilation or placement.")
                elif _health_state["temp"] and temp <= TEMP_CLEAR_C:
                    _health_state["temp"] = False
                    send(f"✅ *Pi temperature back to normal* ({temp:.1f}°C)")

            # Disk space
            disk = _disk_percent()
            if disk is not None:
                if not _health_state["disk"] and disk >= DISK_WARN_PCT:
                    _health_state["disk"] = True
                    send(f"💾 *SD card almost full*\n{disk}% used. Free up space soon to avoid failures.")
                elif _health_state["disk"] and disk < DISK_WARN_PCT - 5:
                    _health_state["disk"] = False
                    send(f"✅ *Disk space recovered* ({disk}% used)")

            # Power/throttle
            throttled = _is_throttled_now()
            if throttled and not _health_state["throttle"]:
                _health_state["throttle"] = True
                send("⚡ *Pi is being throttled*\nLikely an underpowered supply or overheating. Check the power adapter.")
            elif not throttled and _health_state["throttle"]:
                _health_state["throttle"] = False
                send("✅ *Pi throttling cleared*")
        except Exception as e:
            print(f"[health] alert loop error: {e}")


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
    global connected_ip, _startup_msg_id
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
                    adb_manager.keep_awake(ip)
                    adb_manager.lock_to_youtube(ip)
                    print(f"[monitor] Fire Stick {ip} connected.")
                    if _startup_msg_id:
                        wifi = _is_wifi_connected()
                        edit_message(
                            _startup_msg_id,
                            f"✅ *grandmapi is online* 🏠\n\n"
                            f"{'✅' if wifi else '❌'} WiFi: {'Connected' if wifi else 'Disconnected'}\n"
                            f"✅ Fire Stick: {name} (`{ip}`)\n\n"
                            f"Type /help for available commands."
                        )
                        _startup_msg_id = None
                    elif was_connected:
                        send(f"✅ *Fire Stick reconnected*\n\n*{name}* (`{ip}`)")
                    was_connected = False
                    break
        time.sleep(30 if not connected_ip else FS_CHECK_INTERVAL)


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
    threading.Thread(target=_health_alert_loop, daemon=True).start()


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
            adb_manager.keep_awake(ip)
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
    "/resetyt — Force-restart YouTube to home screen\n"
    "/history — Open YouTube watch history\n"
    "/ytlock — Lock to YouTube only\n"
    "/ytunlock — Restore normal access\n"
    "/screenshot — Capture the screen\n"
    "/live — Live screen view (browser link)\n"
    "/restarttv — Reconnect ADB to Fire Stick\n"
    "/reboot — Reboot the Fire Stick\n\n"
    "*🖥 Pi*\n"
    "/health — CPU, RAM, disk, temp, uptime\n"
    "/restart — Restart grandmapi services\n"
    "/update — Pull latest code and restart\n"
    "/logs — Show recent service logs\n\n"
    "*ℹ️ Info*\n"
    "/status — Connection status\n"
    "/heartbeat — Send daily check-in now\n"
    "/support — Simulate grandma needs help\n"
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
            f"{lock_line}\n"
            f"🏠 LAN IP: `{_lan_ip() or 'unknown'}`"
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

    elif cmd == "/live":
        ts_ip = _tailscale_ip()
        if ts_ip:
            url = f"http://{ts_ip}:{STREAM_PORT}"
            send(
                f"📡 *Live Fire Stick view*\n\n"
                f"Open in your browser (must be on Tailscale):\n{url}\n\n"
                f"_Updates ~1–2 fps. The stream is idle until you open it._"
            )
        else:
            send("❌ Tailscale IP unavailable — can't build the live view link.")

    elif cmd == "/youtube":
        if not ensure_connected():
            return
        alert_id = _read_alert_msg_id()
        adb_manager.open_youtube(connected_ip)
        if alert_id:
            _update_alert_caption(alert_id, _alert_caption("▶️ YouTube opened"))
        else:
            send("▶️ YouTube opened.")

    elif cmd == "/resetyt":
        if not ensure_connected():
            return
        alert_id = _read_alert_msg_id()
        if alert_id:
            _update_alert_caption(alert_id, _alert_caption("⏳ Restarting YouTube..."))
        else:
            send("🔄 Restarting YouTube...")
        adb_manager.restart_youtube(connected_ip)
        path = adb_manager.screenshot(connected_ip)
        if alert_id:
            if path:
                edit_photo_message(alert_id, path, _alert_caption("▶️ YouTube restarted"))
            else:
                _update_alert_caption(alert_id, _alert_caption("▶️ YouTube restarted"))
        elif path:
            send_photo(path, _alert_caption("▶️ YouTube restarted"), reply_markup=REMOTE_KEYBOARD)

    elif cmd == "/history":
        if not ensure_connected():
            return
        alert_id = _read_alert_msg_id()
        if alert_id:
            _update_alert_caption(alert_id, _alert_caption("⏳ Opening YouTube..."))
        else:
            send("📺 Opening YouTube history...")

        # Step 1: launch YouTube, show home screen as progress update
        adb_manager.restart_youtube(connected_ip, settle=2.5)
        if alert_id:
            path = adb_manager.screenshot(connected_ip)
            if path:
                edit_photo_message(alert_id, path, _alert_caption("⏳ Navigating to history..."))

        # Step 2: navigate to history, show final state
        adb_manager.navigate_to_history(connected_ip)
        path = adb_manager.screenshot(connected_ip)
        if alert_id:
            if path:
                edit_photo_message(alert_id, path, _alert_caption("📺 History ready — press OK to play"))
            else:
                _update_alert_caption(alert_id, _alert_caption("📺 History ready — press OK to play"))
        elif path:
            send_photo(path, "📺 History ready — press OK to play", reply_markup=REMOTE_KEYBOARD)

    elif cmd == "/ytlock":
        if not ensure_connected():
            return
        alert_id = _read_alert_msg_id()
        _ytlock_active = True
        adb_manager.lock_to_youtube(connected_ip)
        if alert_id:
            _update_alert_caption(alert_id, _alert_caption("🔒 YouTube locked"))
        else:
            send("🔒 Fire Stick locked to YouTube. It will relaunch automatically if closed.")

    elif cmd == "/ytunlock":
        if not ensure_connected():
            return
        alert_id = _read_alert_msg_id()
        _ytlock_active = False
        adb_manager.unlock(connected_ip)
        if alert_id:
            _update_alert_caption(alert_id, _alert_caption("🔓 YouTube unlocked"))
        else:
            send("🔓 Fire Stick unlocked — normal access restored.")

    elif cmd == "/reboot":
        if not ensure_connected():
            return
        send("🔄 *Rebooting Fire Stick...*\nIt will reconnect automatically in about 30 seconds.")
        adb_manager._adb(connected_ip, "shell", "reboot")
        connected_ip = None

    elif cmd == "/health":
        _, ts_ip, disk, memory = _health_report()
        send(
            f"🖥 *Pi health*\n\n"
            f"{_cpu_temp()}\n"
            f"💻 Load: `{_cpu_load()}`\n"
            f"🧠 RAM: {memory}\n"
            f"💾 Disk: {disk}\n"
            f"⏱ Uptime: {_uptime()}\n"
            f"{_wifi_signal()}\n"
            f"⚡ Throttle: {_throttle_status()}\n"
            f"🏠 LAN IP: `{_lan_ip() or 'unknown'}`\n"
            f"🌐 Tailscale: `{ts_ip}`"
        )

    elif cmd == "/support":
        notify.send(connected_ip)

    elif cmd == "/restarttv":
        if connected_ip:
            adb_manager.disconnect(connected_ip)
            connected_ip = None
        send("🔄 *Fire Stick ADB reset.*\nReconnecting automatically within 30 seconds...")

    elif cmd == "/restart":
        send("🔄 *Restarting grandmapi services...*\nBack online in a few seconds.")
        subprocess.Popen(["sudo", "systemctl", "restart", "grandmapi-telegram", "grandmapi-monitor"])

    elif cmd == "/update":
        send("⬇️ *Pulling latest code...*")
        result = subprocess.run(
            ["git", "-C", "/home/admin/grandmapi", "pull"],
            capture_output=True, text=True
        )
        out = (result.stdout.strip() or result.stderr.strip())[:800]
        send(f"```\n{out}\n```\n\n🔄 Restarting services...")
        subprocess.Popen(["sudo", "systemctl", "restart", "grandmapi-telegram", "grandmapi-monitor"])

    elif cmd == "/logs":
        parts = []
        for svc in ["grandmapi-telegram", "grandmapi-monitor", "grandmapi-wifi"]:
            result = subprocess.run(
                ["sudo", "journalctl", "-u", svc, "--no-pager", "-n", "10"],
                capture_output=True, text=True
            )
            out = result.stdout.strip()[-600:] if result.stdout.strip() else "(no output)"
            parts.append(f"*{svc}:*\n```\n{out}\n```")
        send("\n\n".join(parts))

    else:
        send(f"❓ Unknown command: `{cmd}`\n\nType /help to see all available commands.")


# ------------------------------------------------------------------ main loop

def run():
    if not BOT_TOKEN or not CHAT_ID:
        print("Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID not set in /etc/grandmapi.env")
        sys.exit(1)

    global connected_ip, _startup_msg_id
    print("grandmapi bot started.")
    wifi = _is_wifi_connected()

    # Try to connect to Fire Stick before sending startup message
    devices = discovery.scan()
    for ip in devices:
        if adb_manager.connect(ip):
            connected_ip = ip
            adb_manager.disable_voice(ip)
            adb_manager.keep_awake(ip)
            adb_manager.lock_to_youtube(ip)
            print(f"[startup] Connected to Fire Stick at {ip}")
            break

    if connected_ip:
        fs_name = adb_manager.get_device_name(connected_ip)
        fs_line = f"✅ Fire Stick: {fs_name} (`{connected_ip}`)"
    else:
        fs_line = "🔄 Fire Stick: Scanning..."

    msg_id = send(
        f"✅ *grandmapi is online* 🏠\n\n"
        f"{'✅' if wifi else '❌'} WiFi: {'Connected' if wifi else 'Disconnected'}\n"
        f"{fs_line}\n\n"
        f"Type /help for available commands."
    )
    pin_message(msg_id)
    if not connected_ip:
        _startup_msg_id = msg_id

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
