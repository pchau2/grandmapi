import subprocess
import os
import time


def _adb(ip, *args, timeout=15):
    cmd = ["adb", "-s", f"{ip}:5555"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.returncode == 0, result.stdout, result.stderr


def connect(ip):
    result = subprocess.run(
        ["adb", "connect", f"{ip}:5555"],
        capture_output=True, text=True, timeout=10
    )
    output = result.stdout.lower()
    return "connected" in output or "already connected" in output


def disconnect(ip):
    subprocess.run(["adb", "disconnect", f"{ip}:5555"], capture_output=True)


def screenshot(ip, save_path="/tmp/firestick_screen.png"):
    # Save to sdcard first then pull — more reliable than exec-out on Fire TV
    ok, _, _ = _adb(ip, "shell", "screencap", "-p", "/sdcard/screen.png", timeout=15)
    if ok:
        result = subprocess.run(
            ["adb", "-s", f"{ip}:5555", "pull", "/sdcard/screen.png", save_path],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            _adb(ip, "shell", "rm", "/sdcard/screen.png")
            return save_path

    # Fall back to exec-out
    result = subprocess.run(
        ["adb", "-s", f"{ip}:5555", "exec-out", "screencap", "-p"],
        capture_output=True, timeout=15
    )
    if result.returncode == 0 and len(result.stdout) > 1000:
        with open(save_path, "wb") as f:
            f.write(result.stdout)
        return save_path

    return None


YOUTUBE_PKG = "com.amazon.firetv.youtube"
YOUTUBE_ACTIVITY = "com.amazon.firetv.youtube/dev.cobalt.app.MainActivity"


def open_youtube(ip):
    ok, _, _ = _adb(ip, "shell", "am", "start", "-n", YOUTUBE_ACTIVITY)
    if not ok:
        # Fallback: monkey launch
        _adb(ip, "shell", "monkey", "-p", YOUTUBE_PKG, "1")


def is_youtube_foreground(ip):
    ok, out, _ = _adb(ip, "shell", "dumpsys", "activity", "activities", timeout=5)
    if ok:
        for line in out.split("\n"):
            if "mResumedActivity" in line:
                return YOUTUBE_PKG in line
    return False


def open_youtube_history(ip):
    """Open YouTube history page and play the most recent video."""
    # Launch YouTube with the history deep link
    ok, _, _ = _adb(ip, "shell", "am", "start", "-n", YOUTUBE_ACTIVITY,
                    "-d", "https://www.youtube.com/feed/history")
    if not ok:
        open_youtube(ip)
        time.sleep(4)
    else:
        # Wait for the history page to load
        time.sleep(4)

    # The first video in history is typically focused — press SELECT to play it.
    # Send DOWN once first in case the page header is focused, then SELECT.
    send_key(ip, KEY_DOWN)
    time.sleep(0.5)
    send_key(ip, KEY_SELECT)


def lock_to_youtube(ip):
    open_youtube(ip)


def unlock(ip):
    pass  # keep-alive loop in bot.py handles the lock state


def get_device_name(ip):
    ok, out, _ = _adb(ip, "shell", "settings", "get", "global", "device_name")
    if ok and out.strip():
        return out.strip()
    return ip


def send_key(ip, keycode):
    _adb(ip, "shell", "input", "keyevent", str(keycode))


def disable_voice(ip):
    """Disable Amazon voice search so mic button does nothing on Fire Stick."""
    _adb(ip, "shell", "pm", "disable-user", "--user", "0", "com.amazon.bueller")
    _adb(ip, "shell", "pm", "disable-user", "--user", "0", "com.amazon.alexaautomotiveclientservice")
    _adb(ip, "shell", "pm", "disable-user", "--user", "0", "com.amazon.dee.app")


def enable_voice(ip):
    """Re-enable Amazon voice search."""
    _adb(ip, "shell", "pm", "enable", "com.amazon.bueller")
    _adb(ip, "shell", "pm", "enable", "com.amazon.alexaautomotiveclientservice")
    _adb(ip, "shell", "pm", "enable", "com.amazon.dee.app")


# Common Fire TV keycodes
KEY_HOME = 3
KEY_BACK = 4
KEY_UP = 19
KEY_DOWN = 20
KEY_LEFT = 21
KEY_RIGHT = 22
KEY_SELECT = 23
KEY_PLAY_PAUSE = 85
KEY_VOLUME_UP = 24
KEY_VOLUME_DOWN = 25
