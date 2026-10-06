#!/bin/bash
set -e

echo "Setting up grandmapi..."

# Enable SSH on every boot
sudo systemctl enable ssh
sudo systemctl start ssh

# Set WiFi regulatory domain
sudo raspi-config nonint do_wifi_country US

# Install WiFi GUI dependencies
sudo apt-get update
sudo apt-get install -y python3-tk python3-pip

echo "Setup complete. Reboot to apply all changes."
