#!/usr/bin/env python3
"""
Live web remote for the Fire Stick, viewable in a browser over Tailscale.

Serves an MJPEG stream of the screen (roughly 1-2 fps on a Pi Zero 2 W)
alongside a control pad. Pressing a button sends the keyevent over ADB and
you watch the result update live in the same page — no screenshot lag.

The stream is idle (no ADB load) whenever no browser has the page open.

Usage:
    python3 stream_server.py [port]
"""
import os
import sys
import time
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from firestick import adb_manager, discovery

PORT = 8080
FRAME_INTERVAL = 0.0   # capture back-to-back; screencap speed is the real limit

# Frame size/quality — smaller + lower quality = faster. Tunable via env.
STREAM_WIDTH = int(os.environ.get("STREAM_WIDTH", "480"))
STREAM_QUALITY = int(os.environ.get("STREAM_QUALITY", "45"))

_firestick_ip = None
_ip_lock = threading.Lock()

# Button action -> handler. Key actions send a keyevent; the rest run a helper.
KEY_ACTIONS = {
    "up": adb_manager.KEY_UP,
    "down": adb_manager.KEY_DOWN,
    "left": adb_manager.KEY_LEFT,
    "right": adb_manager.KEY_RIGHT,
    "select": adb_manager.KEY_SELECT,
    "home": adb_manager.KEY_HOME,
    "back": adb_manager.KEY_BACK,
    "play": adb_manager.KEY_PLAY_PAUSE,
    "vol_up": adb_manager.KEY_VOLUME_UP,
    "vol_down": adb_manager.KEY_VOLUME_DOWN,
    "rew": 89,   # KEYCODE_MEDIA_REWIND
    "fwd": 90,   # KEYCODE_MEDIA_FAST_FORWARD
}

PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fire Stick — live remote</title>
<style>
  :root { --b:#2a2a2e; --bh:#3a3a40; --accent:#4a90d9; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; background:#111; color:#eee; font-family:system-ui,sans-serif;
         display:flex; flex-direction:column; align-items:center; gap:14px;
         padding:12px; }
  #screen { width:100%; max-width:640px; background:#000; border-radius:8px;
            aspect-ratio:16/9; object-fit:contain; }
  .pad { width:100%; max-width:360px; display:flex; flex-direction:column; gap:8px; }
  .row { display:flex; gap:8px; justify-content:center; }
  button { flex:1; padding:16px 0; font-size:20px; border:none; border-radius:10px;
           background:var(--b); color:#eee; cursor:pointer; user-select:none;
           transition:background .1s; }
  button:active { background:var(--accent); }
  .wide { font-size:15px; padding:14px 0; }
  .ok { background:#365; }
  #toast { position:fixed; bottom:14px; left:50%; transform:translateX(-50%);
           background:#000a; padding:8px 16px; border-radius:20px; font-size:14px;
           opacity:0; transition:opacity .2s; pointer-events:none; }
  #toast.show { opacity:1; }
</style>
</head>
<body>
  <img id="screen" src="/mjpeg" alt="Fire Stick screen">

  <div class="pad">
    <div class="row"><button onclick="k('up')">▲</button></div>
    <div class="row">
      <button onclick="k('left')">◀</button>
      <button class="ok" onclick="k('select')">OK</button>
      <button onclick="k('right')">▶</button>
    </div>
    <div class="row"><button onclick="k('down')">▼</button></div>
    <div class="row">
      <button class="wide" onclick="k('home')">🏠 Home</button>
      <button class="wide" onclick="k('back')">↩ Back</button>
    </div>
    <div class="row">
      <button onclick="k('rew')">⏮</button>
      <button onclick="k('play')">⏯</button>
      <button onclick="k('fwd')">⏭</button>
    </div>
    <div class="row">
      <button onclick="k('vol_up')">🔊+</button>
      <button onclick="k('vol_down')">🔊–</button>
    </div>
    <div class="row">
      <button class="wide" onclick="k('youtube')">▶️ YouTube</button>
      <button class="wide" onclick="k('resetyt')">🔄 Reset YT</button>
    </div>
    <div class="row">
      <button class="wide" onclick="k('history')">📺 History</button>
    </div>
  </div>

  <div id="toast"></div>
<script>
  const toast = document.getElementById('toast');
  let t;
  function flash(msg) {
    toast.textContent = msg; toast.classList.add('show');
    clearTimeout(t); t = setTimeout(() => toast.classList.remove('show'), 900);
  }
  function k(action) {
    flash(action);
    fetch('/control?action=' + action).catch(() => flash('⚠️ failed'));
  }
</script>
</body>
</html>
"""


def _ensure_ip():
    """Return a currently-reachable Fire Stick IP, re-scanning if the cached one died."""
    global _firestick_ip
    with _ip_lock:
        if _firestick_ip:
            ok, _, _ = adb_manager._adb(_firestick_ip, "shell", "echo", "ok", timeout=3)
            if ok:
                return _firestick_ip
            _firestick_ip = None
        try:
            for ip in discovery.scan():
                if adb_manager.connect(ip):
                    _firestick_ip = ip
                    print(f"[stream] Connected to Fire Stick at {ip}", flush=True)
                    return ip
        except Exception as e:
            print(f"[stream] scan error: {e}", flush=True)
        return None


def _do_action(action):
    """Run a control-pad action. Returns True on success."""
    ip = _firestick_ip or _ensure_ip()
    if not ip:
        return False
    try:
        if action in KEY_ACTIONS:
            adb_manager.send_key(ip, KEY_ACTIONS[action])
        elif action == "youtube":
            adb_manager.open_youtube(ip)
        elif action == "resetyt":
            adb_manager.restart_youtube(ip)
        elif action == "history":
            threading.Thread(target=adb_manager.open_youtube_history,
                             args=(ip,), daemon=True).start()
        else:
            return False
        return True
    except Exception as e:
        print(f"[stream] action '{action}' error: {e}", flush=True)
        return False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep journald quiet

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/mjpeg":
            self._stream()
        elif parsed.path == "/control":
            qs = urllib.parse.parse_qs(parsed.query)
            action = (qs.get("action") or [""])[0]
            ok = _do_action(action)
            self._text(200 if ok else 503, "ok" if ok else "unavailable")
        else:
            self._html(PAGE)

    def _text(self, code, msg):
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        misses = 0
        try:
            while True:
                ip = _firestick_ip or _ensure_ip()
                frame = ctype = None
                if ip:
                    frame, ctype = adb_manager.capture_jpeg_bytes(
                        ip, max_width=STREAM_WIDTH, quality=STREAM_QUALITY)
                if frame:
                    misses = 0
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(
                        f"Content-Type: {ctype}\r\n"
                        f"Content-Length: {len(frame)}\r\n\r\n".encode()
                    )
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                else:
                    misses += 1
                    if misses >= 3:
                        _ensure_ip()  # cached IP likely stale; re-resolve
                        misses = 0
                    time.sleep(1)
                time.sleep(FRAME_INTERVAL)
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser closed the tab
        except Exception as e:
            print(f"[stream] frame error: {e}", flush=True)


def main():
    global PORT
    if len(sys.argv) > 1:
        PORT = int(sys.argv[1])

    # Resolve the Fire Stick before serving; keep retrying so the service
    # survives being started before the Fire Stick is online.
    while not _ensure_ip():
        print("[stream] Fire Stick not found yet, retrying in 10s...", flush=True)
        time.sleep(10)

    print(f"[stream] Serving live remote on port {PORT}", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
