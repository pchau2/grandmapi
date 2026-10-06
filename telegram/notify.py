#!/usr/bin/env python3
import urllib.request
import urllib.parse
import json
import os
import sys
import time

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ALERT_TEXT = (
    "🆘 *GRANDMA NEEDS HELP!*\n\n"
    "*Quick actions:*\n"
    "/resetyt — Restart YouTube to home screen\n"
    "/history — Open YouTube history\n"
    "/youtube — Open YouTube\n"
    "/ytlock — Lock to YouTube\n"
    "/ytunlock — Restore normal access"
)

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

ALERT_SCREEN_PATH = "/tmp/grandmapi_alert_screen.png"
ALERT_MSG_ID_PATH = "/tmp/grandmapi_alert_msg_id"


def _send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": REMOTE_KEYBOARD
    }).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
        return result.get("result", {}).get("message_id")


def _send_photo(path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    boundary = "grandmapiboundary"
    with open(path, "rb") as f:
        photo_data = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{CHAT_ID}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="parse_mode"\r\n\r\n'
        f"Markdown\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="caption"\r\n\r\n'
        f"{caption}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="reply_markup"\r\n\r\n'
        f"{REMOTE_KEYBOARD}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="screen.png"\r\n'
        f"Content-Type: {'image/jpeg' if path.lower().endswith('.jpg') else 'image/png'}\r\n\r\n"
    ).encode() + photo_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
        return result.get("result", {}).get("message_id")


def send(ip=None):
    if not BOT_TOKEN or not CHAT_ID:
        print("Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID not set")
        return False

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from firestick import adb_manager

    screenshot_path = None
    if ip:
        # Small delay so the mic overlay finishes dismissing before we screencap
        time.sleep(0.8)
        for attempt in range(2):
            try:
                screenshot_path = adb_manager.screenshot(ip, save_path=ALERT_SCREEN_PATH)
                if screenshot_path:
                    print(f"[notify] Screenshot captured on attempt {attempt + 1}")
                    break
                print(f"[notify] Screenshot returned None on attempt {attempt + 1}")
            except Exception as e:
                print(f"[notify] Screenshot attempt {attempt + 1} failed: {e}")
            time.sleep(1)

    try:
        if screenshot_path:
            msg_id = _send_photo(screenshot_path, ALERT_TEXT)
        else:
            msg_id = _send_message(ALERT_TEXT)
        if msg_id:
            try:
                with open(ALERT_MSG_ID_PATH, "w") as f:
                    f.write(str(msg_id))
            except Exception:
                pass
        return True
    except Exception as e:
        print(f"[notify] Failed to send alert: {e}")
        try:
            _send_message(ALERT_TEXT)
        except Exception:
            pass
        return False


if __name__ == "__main__":
    ok = send()
    print("Alert sent!" if ok else "Failed to send alert.")
