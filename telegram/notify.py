#!/usr/bin/env python3
import urllib.request
import urllib.parse
import json
import os
import sys

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ALERT_TEXT = (
    "🆘 *GRANDMA NEEDS HELP!*\n\n"
    "She pressed the mic button on the remote.\n\n"
    "*Quick actions:*\n"
    "/screenshot — See what's on screen\n"
    "/history — Resume most recent video\n"
    "/youtube — Open YouTube\n"
    "/ytlock — Lock to YouTube\n"
    "/ytunlock — Restore normal access\n"
    "/status — Check connection\n"
    "/help — All commands"
)


def _send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read()).get("ok", False)


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
        f'Content-Disposition: form-data; name="photo"; filename="screen.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + photo_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()).get("ok", False)


def send(ip=None):
    if not BOT_TOKEN or not CHAT_ID:
        print("Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID not set")
        return False

    screenshot_path = None
    if ip:
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from firestick import adb_manager
            screenshot_path = adb_manager.screenshot(ip)
        except Exception as e:
            print(f"Screenshot failed: {e}")

    try:
        if screenshot_path:
            return _send_photo(screenshot_path, ALERT_TEXT)
        else:
            return _send_message(ALERT_TEXT)
    except Exception as e:
        print(f"Failed to send alert: {e}")
        try:
            return _send_message(ALERT_TEXT)
        except Exception:
            return False


if __name__ == "__main__":
    ok = send()
    print("Alert sent!" if ok else "Failed to send alert.")
