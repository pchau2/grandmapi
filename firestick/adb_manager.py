import subprocess
import os


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
    ok, _, _ = _adb(ip, "exec-out", "screencap", "-p",
                    timeout=15)
    # exec-out with screencap needs binary output
    result = subprocess.run(
        ["adb", "-s", f"{ip}:5555", "exec-out", "screencap", "-p"],
        capture_output=True, timeout=15
    )
    if result.returncode == 0 and result.stdout:
        with open(save_path, "wb") as f:
            f.write(result.stdout)
        return save_path
    return None


def open_youtube(ip):
    ok, _, _ = _adb(ip, "shell", "am", "start",
                    "-a", "android.intent.action.VIEW",
                    "-d", "https://www.youtube.com",
                    "-n", "com.amazon.firetv.youtube/com.amazon.firetv.youtube.app.YouTubeActivity")
    if not ok:
        # Fallback: launch via package
        _adb(ip, "shell", "monkey", "-p", "com.amazon.firetv.youtube", "-c",
             "android.intent.category.LAUNCHER", "1")


def lock_to_youtube(ip):
    # Disable Fire TV home launcher
    _adb(ip, "shell", "pm", "disable-user", "--user", "0",
         "com.amazon.firetv.fireflyui")
    # Set YouTube as default home
    _adb(ip, "shell", "cmd", "package", "set-home-activity",
         "com.amazon.firetv.youtube/com.amazon.firetv.youtube.app.YouTubeActivity")
    open_youtube(ip)


def unlock(ip):
    _adb(ip, "shell", "pm", "enable", "com.amazon.firetv.fireflyui")
    _adb(ip, "shell", "cmd", "package", "set-home-activity",
         "com.amazon.firetv.fireflyui/.ui.HomeActivity")


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
