# grandmapi

A Raspberry Pi Zero 2 W device for grandma. Press a button → sends a Telegram message requesting tech support. Includes a TV-friendly WiFi setup GUI controlled via Argon IR remote.

## Hardware

- Raspberry Pi Zero 2 W
- Argon IR remote (HDMI-CEC via TV)
- TV connected via HDMI

## Features

- **WiFi Setup GUI** — on-screen network browser and password entry, navigated with arrow keys / IR remote
- **Telegram Alert** — press a button to send "GRANDMA REQUESTING TECH SUPPORT!!!" via Telegram
- **SSH** — auto-enabled on every boot
- **Tailscale** — remote access from anywhere

## First-Time Setup

### 1. Flash Pi OS

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Flash **Raspberry Pi OS (64-bit)** to your SD card
3. In Imager's settings (gear icon) configure:
   - Hostname: `raspberrypi`
   - Username: `admin`, Password: *(your choice)*
   - WiFi SSID + password
   - Enable SSH (password auth)
   - WiFi country: `US`

### 2. Clone the repo on the Pi

SSH into the Pi then run:

```bash
cd ~ && git clone https://github.com/pchau2/grandmapi.git
```

### 3. Run setup

```bash
bash ~/grandmapi/scripts/setup.sh
```

This will:
- Enable SSH on every boot
- Set WiFi regulatory domain to US
- Install pygame
- Register and enable the WiFi GUI service

### 4. Set up Telegram

Create a Telegram bot:
1. Open Telegram → search **@BotFather** → send `/newbot`
2. Follow the prompts to name your bot and get an **API token**
3. Send your bot any message, then open this URL in a browser to get your **chat ID**:
   ```
   https://api.telegram.org/botYOUR_TOKEN/getUpdates
   ```
   Your chat ID is the `id` value inside `"chat"`.

Add your credentials to the Pi:

```bash
sudo nano /etc/grandmapi.env
```

```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

Test it:

```bash
source /etc/grandmapi.env && python3 ~/grandmapi/telegram/notify.py
```

### 5. Set up Tailscale (remote SSH access)

On the Pi:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Open the URL it gives you to authenticate. Install Tailscale on your other devices and sign into the same account. Then SSH from anywhere:

```bash
ssh admin@raspberrypi
```

Or by Tailscale IP:

```bash
ssh admin@100.x.x.x
```

### 6. Set up IR remote (requires TV, not monitor)

Plug the Pi into a TV (not a monitor — monitors don't forward IR over HDMI-CEC). Test CEC is working:

```bash
echo "scan" | cec-client -s -d 1
```

Then monitor for button presses:

```bash
cec-client -d 8
```

Press buttons on the Argon remote — key press events will appear in the output. Remote integration with Telegram and the WiFi GUI will be wired up once CEC key codes are confirmed.

## Structure

```
wifi/
  main.py                   # pygame WiFi setup GUI
  wifi_manager.py           # scan networks, connect via wpa_supplicant
  onscreen_keyboard.py      # on-screen QWERTY keyboard for password entry
  grandmapi-wifi.service    # systemd service (auto-starts GUI on boot)
telegram/
  notify.py                 # sends Telegram alert
  grandmapi.env.template    # credentials template
scripts/
  setup.sh                  # run once after flash
```
