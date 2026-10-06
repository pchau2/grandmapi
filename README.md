# grandmapi

Raspberry Pi Zero 2 W configuration for a TV-connected media device.

## Features

- **WiFi Setup GUI** — on-screen network browser and password entry, controlled via Argon IR remote (HDMI-CEC)
- **SSH** — auto-enabled on every boot

## Structure

```
wifi/       # WiFi onboarding GUI (shows on boot when WiFi is unconfigured)
ssh/        # SSH auto-enable configuration
scripts/    # Setup and utility scripts
```

## Setup

Run once after flashing Pi OS:

```bash
bash scripts/setup.sh
```

## Hardware

- Raspberry Pi Zero 2 W
- Argon IR remote (HDMI-CEC)
