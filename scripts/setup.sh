#!/bin/bash
set -e

echo "Setting up grandmapi..."

# Enable SSH on every boot
sudo systemctl enable ssh
sudo systemctl start ssh

# Set WiFi regulatory domain
sudo raspi-config nonint do_wifi_country US

# Install dependencies
sudo apt-get update
sudo apt-get install -y python3-pygame

# Install WiFi GUI service
sudo cp /home/admin/grandmapi/wifi/grandmapi-wifi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable grandmapi-wifi.service

echo "Setup complete. Reboot to apply all changes."
