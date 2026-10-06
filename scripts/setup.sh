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
sudo apt-get install -y python3-pygame adb

# Install WiFi GUI service
sudo cp /home/admin/grandmapi/wifi/grandmapi-wifi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable grandmapi-wifi.service

# Create env file for Telegram credentials if it doesn't exist
if [ ! -f /etc/grandmapi.env ]; then
    sudo cp /home/admin/grandmapi/telegram/grandmapi.env.template /etc/grandmapi.env
    echo "Edit /etc/grandmapi.env and add your Telegram bot token and chat ID."
fi

echo "Setup complete. Reboot to apply all changes."
