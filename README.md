# Battery Monitor for macOS

A smart battery monitoring daemon for macOS that sends notifications and controls smart devices based on battery thresholds. Perfect for extending your MacBook's battery lifespan and staying aware of charging status.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

## Features

- 🔋 **Real-time battery monitoring** - Tracks battery percentage and charging status
- 📱 **Telegram alerts** - Get notified at 20% (low) and 80% (charged) thresholds
- 🔌 **Kasa Smart Plug control** - Automatically turn charging plugs on/off based on battery levels
- ⚡ **EcoFlow Delta 2 integration** - Control power station DC output via MQTT
- 📊 **Battery history logging** - CSV format logs for analysis
- 🚀 **LaunchAgent support** - Runs automatically on macOS login
- ⚙️ **Configurable intervals** - Customize check frequency (default: 60 seconds)

## Installation

### Prerequisites

- macOS 10.12 or later
- Python 3.8+
- Optional: Smart devices (Kasa KP115 plug, EcoFlow Delta 2)

### Quick Start

```bash
# 1. Clone or download this repository
git clone https://github.com/yourusername/battery-monitor.git
cd battery-monitor

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and configure environment
cp .env.example .env
# Edit .env with your actual API keys and device IPs
nano .env

# 4. Test it out
python3 battery_monitor.py start

# 5. Install as LaunchAgent (auto-start on login)
python3 battery_monitor.py install
```

## Usage

### Commands

| Command | Description |
|---------|-------------|
| `start [--interval N]` | Start monitoring (60s default) |
| `stop` | Stop monitoring |
| `status` | Show current status and stats |
| `install [--interval N]` | Install as LaunchAgent (auto-start) |
| `uninstall` | Remove LaunchAgent |
| `action <action>` | Test integrations manually |

### Examples

```bash
# Start with 30-second check interval
python3 battery_monitor.py start --interval 30

# Stop the monitor
python3 battery_monitor.py stop

# Check status
python3 battery_monitor.py status

# Test Telegram notification
python3 battery_monitor.py action telegram

# Test Kasa plug control
python3 battery_monitor.py action kasa-on
python3 battery_monitor.py action kasa-off

# Test EcoFlow control
python3 battery_monitor.py action ecoflow-dc-on
python3 battery_monitor.py action ecoflow-dc-off
```

## Configuration

Create a `.env` file (copy from `.env.example`) with your settings:

```bash
# Telegram Bot Configuration
# Get these from BotFather on Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# Kasa Smart Plug Configuration (KP115)
# Find IP address in your router or Kasa app
KASA_PLUG_IP=192.168.1.100
KASA_PLUG_USERNAME=your_kasa_email@example.com
KASA_PLUG_PASSWORD=your_kasa_password

# EcoFlow Delta 2 Configuration (optional)
# Get from EcoFlow app and MQTT setup
ECOFLOW_SERIAL=your_serial_number
ECOFLOW_MQTT_HOST=192.168.1.150
ECOFLOW_MQTT_PORT=1883
ECOFLOW_MQTT_USERNAME=your_mqtt_username
ECOFLOW_MQTT_PASSWORD=your_mqtt_password
```

### Environment Variables Priority

1. System environment variables (`export TELEGRAM_BOT_TOKEN=...`)
2. `.env` file in script directory
3. Default values (empty/None)

## Smart Integrations

### Telegram Notifications
Sends alerts when battery crosses thresholds:
- **20%**: "Battery Low" - Time to charge
- **80%**: "Battery Charged" - Consider unplugging for battery health

Requires:
- Telegram bot token (create via [@BotFather](https://t.me/botfather))
- Your Telegram chat ID

### Kasa KP115 Smart Plug
Automates charging device control:
- **At 20% battery**: Turns plug ON to start charging
- **At 80% battery**: Turns plug OFF to preserve device battery

Includes exponential backoff retry logic for network resilience.

### EcoFlow Delta 2
Controls power station DC output port:
- **At 20% battery**: Enables DC port for portable charging
- **At 80% battery**: Disables DC port to save power

Requires MQTT broker access (typically built into EcoFlow devices).

## Logs and Data

Battery monitor stores logs and history in `~/.battery_monitor/`:

```
~/.battery_monitor/
├── battery_history.csv      # Battery percentage history
├── battery_monitor.pid      # Process ID (when running)
├── battery_monitor.out.log  # Standard output logs
├── battery_monitor.err.log  # Error logs
└── config.json             # Configuration
```

View battery history:
```bash
cat ~/.battery_monitor/battery_history.csv
# timestamp,battery_percent,ac_power,charging
# 2024-06-10T08:30:45.123456,75,True,True
# 2024-06-10T08:31:45.654321,75,True,True
```

## Troubleshooting

### "No battery detected" warning
- Running on desktop Mac without internal battery
- Script will still run but won't trigger notifications
- Safe to ignore if intentional

### Telegram notifications not working
```bash
# Test Telegram integration
python3 battery_monitor.py action telegram

# Check configuration
grep TELEGRAM .env

# Verify token is valid and chat ID is correct
```

### Kasa plug not responding
- Verify plug IP address is correct: `ping 192.168.1.100`
- Ensure plug is on same WiFi network
- Check credentials in `.env`
- Test manually: `python3 battery_monitor.py action kasa-on`

### LaunchAgent not starting on login
```bash
# Check if installed
launchctl list | grep batterymonitor

# Check plist syntax
plutil -lint ~/Library/LaunchAgents/com.user.batterymonitor.plist

# View launch logs
log stream --predicate 'eventMessage contains[cd] "batterymonitor"'
```

### Battery thresholds not triggering
- Monitor must be running continuously (start with `install` command)
- Thresholds are: 20% (low), 80% (high)
- Notifications only trigger on threshold crossing, not every check
- View logs: `tail -f ~/.battery_monitor/battery_monitor.err.log`

## Development

### Project Structure

```
battery_monitor/
├── battery_monitor.py      # Main monitoring daemon
├── requirements.txt        # Python dependencies
├── .env.example           # Configuration template
├── .gitignore            # Git ignore rules
├── LICENSE               # MIT License
└── README.md            # This file
```

### Running Tests

```bash
# Manual testing
python3 battery_monitor.py start --interval 10
# Watch output and battery level changes

# Test each integration separately
python3 battery_monitor.py action telegram
python3 battery_monitor.py action kasa-on
```

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Built with Python 3.8+
- Uses [python-kasa](https://github.com/jsimonetti/python-kasa) for Kasa device control
- Uses [paho-mqtt](https://github.com/eclipse/paho.mqtt.python) for MQTT/EcoFlow integration

## Support

For issues, questions, or suggestions:
- Open an [Issue](https://github.com/yourusername/battery-monitor/issues)
- Start a [Discussion](https://github.com/yourusername/battery-monitor/discussions)
- Check existing issues for solutions

---

**Made with ❤️ for MacBook users**
