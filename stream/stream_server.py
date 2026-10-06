#!/usr/bin/env python3
"""
Live web remote for the Fire Stick, viewable in a browser over Tailscale.

Primary path: real H.264 video. The Fire Stick hardware-encodes its screen
via `screenrecord`, the Pi pipes it through ffmpeg into fragmented MP4 (a
container remux, no transcoding — the Pi stays light), and the browser plays
it with Media Source Extensions. This is smooth and low-latency.

Fallback path: if H.264/ffmpeg/MSE is unavailable, the page automatically
switches to a ~1 fps MJPEG screenshot stream so it always shows something.

A control pad sends keyevents over ADB; you watch the result live.
Capture/encoding only runs while a browser has the page open.

Usage:
    python3 stream_server.py [port]
"""
import os
import sys
import time
import shutil
import threading
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from firestick import adb_manager, discovery

PORT = 8080

# H.264 video settings (tunable via env)
H264_SIZE = os.environ.get("STREAM_H264_SIZE", "1280x720")
H264_BITRATE = os.environ.get("STREAM_H264_BITRATE", "4000000")

# MJPEG fallback frame settings (tunable via env)
STREAM_WIDTH = int(os.environ.get("STREAM_WIDTH", "480"))
STREAM_QUALITY = int(os.environ.get("STREAM_QUALITY", "45"))

_firestick_ip = None
_ip_lock = threading.Lock()

# ---- MJPEG fallback: shared latest-frame buffer -------------------------------
_latest = {"frame": None, "ctype": None, "seq": 0}
_frame_lock = threading.Lock()
_viewers = 0
_viewers_lock = threading.Lock()
_capture_thread = None

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
  :root { --b:#2a2a2e; --accent:#4a90d9; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; background:#111; color:#eee; font-family:system-ui,sans-serif;
         display:flex; flex-direction:column; align-items:center; gap:14px;
         padding:12px; }
  #screen { width:100%; max-width:720px; background:#000; border-radius:8px;
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
  #mode { font-size:12px; color:#888; }
</style>
</head>
<body>
  <video id="screen" autoplay muted playsinline></video>
  <div id="mode">connecting…</div>

  <div class="pad">
    <div class="row"><button onclick="k('up')">▲</button></div>
    <div class="row">
      <button onclick="k('left')">◀</button>
      <button class="ok" onclick="k('select')">OK</button>
      <button onclick="k('right')">▶</button>
    </div>
    <div class="row"><button onclick="k('down')">▼</button></div>
    <div class="row">
      <button class="wide" onclick="k('home')">\U0001f3e0 Home</button>
      <button class="wide" onclick="k('back')">↩ Back</button>
    </div>
    <div class="row">
      <button onclick="k('rew')">⏮</button>
      <button onclick="k('play')">⏯</button>
      <button onclick="k('fwd')">⏭</button>
    </div>
    <div class="row">
      <button onclick="k('vol_up')">\U0001f50a+</button>
      <button onclick="k('vol_down')">\U0001f50a–</button>
    </div>
    <div class="row">
      <button class="wide" onclick="k('youtube')">▶️ YouTube</button>
      <button class="wide" onclick="k('resetyt')">\U0001f504 Reset YT</button>
    </div>
    <div class="row">
      <button class="wide" onclick="k('history')">\U0001f4fa History</button>
    </div>
  </div>

  <div id="toast"></div>
<script>
  const toast = document.getElementById('toast');
  const mode = document.getElementById('mode');
  let t;
  function flash(msg) {
    toast.textContent = msg; toast.classList.add('show');
    clearTimeout(t); t = setTimeout(() => toast.classList.remove('show'), 900);
  }
  function k(action) {
    flash(action);
    fetch('/control?action=' + action).catch(() => flash('failed'));
  }

  // Candidate H.264 codec strings to try (profile must match the encoder).
  const CODECS = ['avc1.640029','avc1.64001f','avc1.4d401f','avc1.42e01f'];
  let fellBack = false;

  function fallbackMJPEG(why) {
    if (fellBack) return;
    fellBack = true;
    mode.textContent = 'MJPEG fallback (~1 fps)' + (why ? ' — ' + why : '');
    const old = document.getElementById('screen');
    const img = document.createElement('img');
    img.id = 'screen';
    img.src = '/mjpeg';
    old.replaceWith(img);
  }

  function pickCodec() {
    if (!('MediaSource' in window)) return null;
    for (const c of CODECS) {
      const type = 'video/mp4; codecs="' + c + '"';
      if (MediaSource.isTypeSupported(type)) return type;
    }
    return null;
  }

  function startVideo() {
    const type = pickCodec();
    if (!type) return fallbackMJPEG('no MSE');

    const ms = new MediaSource();
    const v = document.getElementById('screen');
    v.src = URL.createObjectURL(ms);

    ms.addEventListener('sourceopen', async () => {
      let sb;
      try { sb = ms.addSourceBuffer(type); sb.mode = 'sequence'; }
      catch (e) { return fallbackMJPEG('codec'); }

      const queue = [];
      function pump() {
        if (sb.updating || !queue.length) return;
        try { sb.appendBuffer(queue.shift()); }
        catch (e) {
          if (e.name === 'QuotaExceededError') {
            try {
              const b = sb.buffered;
              if (b.length) sb.remove(b.start(0), Math.max(b.start(0), v.currentTime - 1));
            } catch (_) {}
          } else { return fallbackMJPEG('append'); }
        }
      }
      sb.addEventListener('updateend', pump);

      // Keep latency low: stay near the live edge.
      v.addEventListener('timeupdate', () => {
        const b = v.buffered;
        if (b.length && b.end(b.length - 1) - v.currentTime > 2.5) {
          v.currentTime = b.end(b.length - 1) - 0.3;
        }
      });

      let res;
      try { res = await fetch('/video'); }
      catch (e) { return fallbackMJPEG('fetch'); }
      if (!res.ok) return fallbackMJPEG('no ffmpeg');
      mode.textContent = 'H.264 live';

      const reader = res.body.getReader();
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          queue.push(value);
          pump();
        }
      } catch (e) { /* stream dropped */ }

      // screenrecord has a 3-min cap; the pipeline ends and we reconnect.
      try { if (ms.readyState === 'open') ms.endOfStream(); } catch (_) {}
      if (!fellBack) setTimeout(startVideo, 400);
    });

    ms.addEventListener('error', () => fallbackMJPEG('mediasource'));
  }

  startVideo();
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


def _have_ffmpeg():
    return shutil.which("ffmpeg") is not None


def _start_h264(ip):
    """Start screenrecord (device H.264) piped through ffmpeg into fragmented MP4."""
    adb_cmd = [
        "adb", "-s", f"{ip}:5555", "exec-out",
        "screenrecord", "--output-format=h264",
        f"--size={H264_SIZE}", f"--bit-rate={H264_BITRATE}",
        "--time-limit=180", "-",
    ]
    ff_cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-f", "h264", "-i", "pipe:0",
        "-an", "-c:v", "copy",
        "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "pipe:1",
    ]
    p_adb = subprocess.Popen(adb_cmd, stdout=subprocess.PIPE)
    p_ff = subprocess.Popen(ff_cmd, stdin=p_adb.stdout, stdout=subprocess.PIPE)
    p_adb.stdout.close()  # let ffmpeg own the pipe so adb sees SIGPIPE on exit
    return p_adb, p_ff


# ---- MJPEG fallback ----------------------------------------------------------

def _capture_loop():
    """Continuously grab frames into the shared buffer while anyone is watching."""
    global _capture_thread
    misses = 0
    while True:
        with _viewers_lock:
            if _viewers <= 0:
                _capture_thread = None
                return
        ip = _firestick_ip or _ensure_ip()
        frame = ctype = None
        if ip:
            frame, ctype = adb_manager.capture_jpeg_bytes(
                ip, max_width=STREAM_WIDTH, quality=STREAM_QUALITY)
        if frame:
            misses = 0
            with _frame_lock:
                _latest["frame"] = frame
                _latest["ctype"] = ctype
                _latest["seq"] += 1
        else:
            misses += 1
            if misses >= 3:
                _ensure_ip()
                misses = 0
            time.sleep(1)


def _start_capture():
    global _capture_thread
    with _viewers_lock:
        if _capture_thread is None or not _capture_thread.is_alive():
            _capture_thread = threading.Thread(target=_capture_loop, daemon=True)
            _capture_thread.start()


# ---- control pad -------------------------------------------------------------

def _do_action(action):
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
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/video":
            self._video()
        elif parsed.path == "/mjpeg":
            self._mjpeg()
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

    def _video(self):
        ip = _firestick_ip or _ensure_ip()
        if not ip or not _have_ffmpeg():
            self._text(503, "h264 unavailable")
            return
        p_adb = p_ff = None
        try:
            p_adb, p_ff = _start_h264(ip)
        except Exception as e:
            print(f"[stream] h264 start error: {e}", flush=True)
            self._text(503, "h264 start failed")
            return

        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                chunk = p_ff.stdout.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"[stream] h264 relay error: {e}", flush=True)
        finally:
            for p in (p_ff, p_adb):
                if p:
                    try:
                        p.kill()
                    except Exception:
                        pass

    def _mjpeg(self):
        global _viewers
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        with _viewers_lock:
            _viewers += 1
        _start_capture()

        last_seq = -1
        try:
            while True:
                with _frame_lock:
                    seq = _latest["seq"]
                    frame = _latest["frame"]
                    ctype = _latest["ctype"]
                if frame is not None and seq != last_seq:
                    last_seq = seq
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(
                        f"Content-Type: {ctype}\r\n"
                        f"Content-Length: {len(frame)}\r\n\r\n".encode()
                    )
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                else:
                    time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"[stream] mjpeg error: {e}", flush=True)
        finally:
            with _viewers_lock:
                _viewers -= 1


def main():
    global PORT
    if len(sys.argv) > 1:
        PORT = int(sys.argv[1])

    while not _ensure_ip():
        print("[stream] Fire Stick not found yet, retrying in 10s...", flush=True)
        time.sleep(10)

    kind = "H.264" if _have_ffmpeg() else "MJPEG-only (ffmpeg missing)"
    print(f"[stream] Serving live remote ({kind}) on port {PORT}", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
