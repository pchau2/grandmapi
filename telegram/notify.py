#!/usr/bin/env python3
import urllib.request
import urllib.parse
import json
import os

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
MESSAGE = "GRANDMA REQUESTING TECH SUPPORT!!!"


def send():
    if not BOT_TOKEN or not CHAT_ID:
        print("Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in /etc/grandmapi.env")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": MESSAGE}).encode()

    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("ok", False)
    except Exception as e:
        print(f"Failed to send message: {e}")
        return False


if __name__ == "__main__":
    ok = send()
    print("Message sent!" if ok else "Failed to send message.")
