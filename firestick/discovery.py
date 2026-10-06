import socket
import subprocess
import concurrent.futures


ADB_PORT = 5555


def get_local_subnet():
    try:
        result = subprocess.run(
            ["ip", "route"],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            # Look for wlan0 subnet line e.g. "192.168.1.0/24 dev wlan0"
            if "wlan0" in line and "/" in line:
                network = line.split()[0]
                base = ".".join(network.split(".")[:3])
                return base
    except Exception:
        pass
    # Fallback: derive from local IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return ".".join(local_ip.split(".")[:3])
    except Exception:
        return "192.168.1"


def _check_host(ip, port, timeout):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return ip if result == 0 else None
    except Exception:
        return None


def scan(on_progress=None, timeout=0.5):
    subnet = get_local_subnet()
    hosts = [f"{subnet}.{i}" for i in range(1, 255)]
    found = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(_check_host, ip, ADB_PORT, timeout): ip for ip in hosts}
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            if on_progress:
                on_progress(completed, len(hosts))
            result = future.result()
            if result:
                found.append(result)

    return found
