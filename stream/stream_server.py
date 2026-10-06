#!/usr/bin/env python3
"""
Lightweight MJPEG stream of the Fire Stick screen, viewable in a browser
over Tailscale. Captures ADB screenshots as fast as the device allows
(roughly 1-2 fps on a Pi Zero 2 W over 2.4 GHz WiFi) and serves them as a
motion-JPEG stream, which every browser renders as a live-updating image.

Runs as an always-on service. It is idle (no ADB load) whenever no browser
is connected — frames are only captured while someone has the page open.

Usage:
    python3 stream_server.py [port]
"""
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from firestick import adb_manager, discovery

PORT = 8080
FRAME_INTERVAL = 0.1   # minimum gap between captures; capture itself is the real limit

_firestick_ip = None
_ip_lock = threading.Lock()

PAGE = """<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fire Stick — live</title>
<style>
  body { margin:0; background:#111; display:flex; align-items:center;
         justify-content:center; height:100vh; }
  img { max-width:100%; max-height:100vh; }
</style>
</head>
<body>
  <img src="/mjpeg" alt="Fire Stick screen">
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep journald quiet

    def do_GET(self):
        if self.path == "/mjpeg":
            self._stream()
        else:
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
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
                    frame, ctype = adb_manager.capture_jpeg_bytes(ip)
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

    print(f"[stream] Serving on port {PORT}", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
