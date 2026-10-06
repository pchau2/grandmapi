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


def _png_to_jpeg_bytes(png_bytes, max_width, quality):
    """Downscale + JPEG-encode raw PNG bytes. Returns JPEG bytes or None if Pillow unavailable."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        if img.width > max_width:
            h = int(img.height * max_width / img.width)
            img = img.resize((max_width, h))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        return buf.getvalue()
    except Exception:
        return None


def _compress_to_jpeg(png_bytes, save_path, max_width=960, quality=70):
    """Downscale + compress PNG bytes to a JPEG file. Returns path or None if Pillow unavailable."""
    jpeg_bytes = _png_to_jpeg_bytes(png_bytes, max_width, quality)
    if jpeg_bytes is None:
        return None
    jpeg_path = save_path.rsplit(".", 1)[0] + ".jpg"
    with open(jpeg_path, "wb") as f:
        f.write(jpeg_bytes)
    return jpeg_path


def capture_jpeg_bytes(ip, max_width=640, quality=50):
    """Grab one frame as JPEG bytes for live streaming. Falls back to raw PNG bytes."""
    result = subprocess.run(
        ["adb", "-s", f"{ip}:5555", "exec-out", "screencap", "-p"],
        capture_output=True, timeout=15
    )
    if result.returncode == 0 and len(result.stdout) > 1000:
        jpeg = _png_to_jpeg_bytes(result.stdout, max_width, quality)
        if jpeg is not None:
            return jpeg, "image/jpeg"
        return result.stdout, "image/png"
    return None, None


def screenshot(ip, save_path="/tmp/firestick_screen.png"):
    # exec-out pipes screenshot directly — one round trip vs save/pull/delete
    result = subprocess.run(
        ["adb", "-s", f"{ip}:5555", "exec-out", "screencap", "-p"],
        capture_output=True, timeout=15
    )
    if result.returncode == 0 and len(result.stdout) > 1000:
        path = _compress_to_jpeg(result.stdout, save_path)
        if path:
            return path
        with open(save_path, "wb") as f:
            f.write(result.stdout)
        return save_path

    # Fall back to sdcard pull if exec-out fails
    ok, _, _ = _adb(ip, "shell", "screencap", "-p", "/sdcard/screen.png", timeout=15)
    if ok:
        result = subprocess.run(
            ["adb", "-s", f"{ip}:5555", "pull", "/sdcard/screen.png", save_path],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            _adb(ip, "shell", "rm", "/sdcard/screen.png")
            # Try to compress the pulled PNG too
            with open(save_path, "rb") as f:
                png_bytes = f.read()
            return _compress_to_jpeg(png_bytes, save_path) or save_path

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


def _wait_for_youtube(ip, timeout=10, settle=0.5):
    """Poll until YouTube is the resumed activity, then wait settle seconds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok, out, _ = _adb(ip, "shell",
                          "dumpsys activity activities | grep mResumedActivity",
                          timeout=3)
        if ok and YOUTUBE_PKG in out:
            time.sleep(settle)
            return True
        time.sleep(0.5)
    return False


def restart_youtube(ip, settle=0.5):
    """Force-stop and relaunch YouTube, waiting until it is foreground."""
    _adb(ip, "shell", "am", "force-stop", YOUTUBE_PKG)
    time.sleep(2)
    _adb(ip, "shell", "am", "start", "-n", YOUTUBE_ACTIVITY)
    _wait_for_youtube(ip, timeout=8, settle=settle)


def navigate_to_history(ip):
    """Navigate from YouTube home to Watch History, focusing the first video."""
    send_key(ip, KEY_LEFT)   # open sidebar
    time.sleep(1.0)

    # Home → Shorts → Subscriptions → Library
    for _ in range(3):
        send_key(ip, KEY_DOWN)
        time.sleep(0.5)

    send_key(ip, KEY_SELECT)  # open Library / History
    time.sleep(3.5)

    send_key(ip, KEY_DOWN)    # focus first history video (grandma presses OK to play)


def open_youtube_history(ip):
    """Launch YouTube fresh then navigate to Watch History."""
    restart_youtube(ip, settle=2.5)  # extra settle so UI renders before key input
    navigate_to_history(ip)


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
