#!/bin/bash
# Install, enable, and (re)start all grandmapi services so they survive reboots.
# Safe to run repeatedly.
set -e

REPO="/home/admin/grandmapi"

echo "==> Copying service files to /etc/systemd/system/"
sudo cp "$REPO/telegram/grandmapi-telegram.service" /etc/systemd/system/
sudo cp "$REPO/firestick/grandmapi-monitor.service" /etc/systemd/system/
sudo cp "$REPO/wifi/grandmapi-wifi.service"        /etc/systemd/system/
sudo cp "$REPO/stream/grandmapi-stream.service"    /etc/systemd/system/

echo "==> Reloading systemd"
sudo systemctl daemon-reload

echo "==> Enabling services (start on boot)"
sudo systemctl enable grandmapi-telegram grandmapi-monitor grandmapi-wifi grandmapi-stream

echo "==> Restarting services"
sudo systemctl restart grandmapi-telegram grandmapi-monitor grandmapi-wifi grandmapi-stream

echo
echo "==> Status check"
for svc in grandmapi-telegram grandmapi-monitor grandmapi-wifi grandmapi-stream; do
    enabled=$(systemctl is-enabled "$svc" 2>/dev/null || echo "unknown")
    active=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
    printf "  %-24s enabled=%-10s active=%s\n" "$svc" "$enabled" "$active"
done
echo
echo "Done. All three should show enabled=enabled and active=active."
