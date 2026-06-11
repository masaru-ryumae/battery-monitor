#!/usr/bin/env python3
"""
Battery Monitor for macOS with Smart Integrations

Monitors battery percentage and sends notifications at 20% (charge) and 80% (unplug) thresholds.
Includes Telegram alerts, Kasa Smart Plug control, and EcoFlow Delta 2 integration.
"""

import argparse
import asyncio
import csv
import json
import os
import plistlib
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

# Configuration
DEFAULT_CHECK_INTERVAL = 60  # seconds
LOW_BATTERY_THRESHOLD = 20  # %
HIGH_BATTERY_THRESHOLD = 80  # %

# Paths
SCRIPT_DIR = Path(__file__).parent.resolve()
DATA_DIR = Path.home() / ".battery_monitor"
LOG_FILE = DATA_DIR / "battery_history.csv"
PID_FILE = DATA_DIR / "battery_monitor.pid"
CONFIG_FILE = DATA_DIR / "config.json"
LAUNCH_AGENT_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_NAME = "com.user.batterymonitor"
PLIST_FILE = LAUNCH_AGENT_DIR / f"{PLIST_NAME}.plist"
ENV_FILE = SCRIPT_DIR / ".env"

# Try to import optional dependencies
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from kasa import SmartPlug
    HAS_KASA = True
except ImportError:
    HAS_KASA = False

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
except ImportError:
    HAS_MQTT = False


def load_env_file() -> dict:
    """Load environment variables from .env file if it exists."""
    env_vars = {}
    if ENV_FILE.exists():
        try:
            with open(ENV_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        env_vars[key.strip()] = value.strip().strip('"\'')
        except Exception as e:
            print(f"Warning: Could not read .env file: {e}", file=sys.stderr)
    return env_vars

# Load environment variables
ENV_VARS = load_env_file()

# Telegram configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or ENV_VARS.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or ENV_VARS.get("TELEGRAM_CHAT_ID")

# Kasa Smart Plug configuration
KASA_PLUG_IP = os.getenv("KASA_PLUG_IP") or ENV_VARS.get("KASA_PLUG_IP")
KASA_PLUG_USERNAME = os.getenv("KASA_PLUG_USERNAME") or ENV_VARS.get("KASA_PLUG_USERNAME")
KASA_PLUG_PASSWORD = os.getenv("KASA_PLUG_PASSWORD") or ENV_VARS.get("KASA_PLUG_PASSWORD")

# EcoFlow configuration
ECOFLOW_SERIAL = os.getenv("ECOFLOW_SERIAL") or ENV_VARS.get("ECOFLOW_SERIAL")
ECOFLOW_MQTT_HOST = os.getenv("ECOFLOW_MQTT_HOST") or ENV_VARS.get("ECOFLOW_MQTT_HOST")
ECOFLOW_MQTT_PORT = int(os.getenv("ECOFLOW_MQTT_PORT") or ENV_VARS.get("ECOFLOW_MQTT_PORT", "1883"))
ECOFLOW_MQTT_USERNAME = os.getenv("ECOFLOW_MQTT_USERNAME") or ENV_VARS.get("ECOFLOW_MQTT_USERNAME")
ECOFLOW_MQTT_PASSWORD = os.getenv("ECOFLOW_MQTT_PASSWORD") or ENV_VARS.get("ECOFLOW_MQTT_PASSWORD")


class TelegramNotifier:
    """Send notifications via Telegram Bot API."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send_message(self, text: str) -> bool:
        """Send a message to Telegram."""
        if not HAS_REQUESTS:
            print("Error: requests library not installed. Install with: pip install requests", file=sys.stderr)
            return False

        if not self.bot_token or "..." in self.bot_token:
            print("Warning: Telegram bot token not configured", file=sys.stderr)
            return False

        try:
            data = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML"
            }
            response = requests.post(self.api_url, json=data, timeout=10)
            if response.status_code == 200:
                return True
            else:
                print(f"Telegram API error: {response.status_code}", file=sys.stderr)
                return False
        except Exception as e:
            print(f"Error sending Telegram message: {e}", file=sys.stderr)
            return False


class KasaPlugController:
    """Control Kasa Smart Plug KP115 with retry logic for network resilience."""

    # Retry configuration
    MAX_RETRIES = 3
    BASE_DELAY = 2.0  # seconds
    MAX_DELAY = 30.0  # seconds

    def __init__(self, ip: str, username: str = None, password: str = None):
        self.ip = ip
        self.username = username
        self.password = password
        self._plug = None
        self._plug_connected = False

    async def _get_plug(self) -> Optional[SmartPlug]:
        """Get or create a cached plug connection."""
        if not HAS_KASA:
            return None

        if self._plug and self._plug_connected:
            try:
                # Test if connection is still alive
                await self._plug.update()
                return self._plug
            except Exception:
                self._plug_connected = False
                self._plug = None

        # Create new connection
        try:
            self._plug = SmartPlug(self.ip)
            await self._plug.update()
            self._plug_connected = True
            return self._plug
        except Exception as e:
            print(f"Error connecting to Kasa plug at {self.ip}: {e}", file=sys.stderr)
            self._plug = None
            self._plug_connected = False
            return None

    async def _execute_with_retry(self, operation, *args, **kwargs) -> bool:
        """Execute an async operation with exponential backoff retry."""
        last_exception = None

        for attempt in range(self.MAX_RETRIES):
            try:
                result = await operation(*args, **kwargs)
                if result:
                    return True
                # If operation returned False (not exception), don't retry
                return False
            except Exception as e:
                last_exception = e
                if attempt < self.MAX_RETRIES - 1:
                    delay = min(self.BASE_DELAY * (2 ** attempt), self.MAX_DELAY)
                    print(f"Kasa operation failed (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}. Retrying in {delay:.1f}s...", file=sys.stderr)
                    await asyncio.sleep(delay)
                    # Force reconnection on next attempt
                    self._plug_connected = False
                    self._plug = None
                else:
                    print(f"Kasa operation failed after {self.MAX_RETRIES} attempts: {e}", file=sys.stderr)

        return False

    async def turn_on(self) -> bool:
        """Turn on the plug with retry logic."""
        if not HAS_KASA:
            print("Error: kasa library not installed. Install with: pip install python-kasa", file=sys.stderr)
            return False

        async def _turn_on():
            plug = await self._get_plug()
            if not plug:
                return False
            await plug.turn_on()
            return True

        return await self._execute_with_retry(_turn_on)

    async def turn_off(self) -> bool:
        """Turn off the plug with retry logic."""
        if not HAS_KASA:
            print("Error: kasa library not installed. Install with: pip install python-kasa", file=sys.stderr)
            return False

        async def _turn_off():
            plug = await self._get_plug()
            if not plug:
                return False
            await plug.turn_off()
            return True

        return await self._execute_with_retry(_turn_off)

    async def get_state(self) -> Optional[bool]:
        """Get current plug state (True=on, False=off) with retry logic."""
        if not HAS_KASA:
            return None

        async def _get_state():
            plug = await self._get_plug()
            if not plug:
                return None
            await plug.update()
            return plug.is_on

        # For get_state, we want to return the actual state, not just bool
        return await self._execute_with_retry(_get_state)


class EcoFlowController:
    """Control EcoFlow Delta 2 via MQTT."""

    def __init__(self, serial: str, mqtt_host: str, mqtt_port: int = 1883,
                 mqtt_username: str = None, mqtt_password: str = None):
        self.serial = serial
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_username = mqtt_username
        self.mqtt_password = mqtt_password
        self.client = None

    def _create_client(self) -> mqtt.Client:
        """Create and configure MQTT client."""
        if not HAS_MQTT:
            return None

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        if self.mqtt_username and self.mqtt_password:
            client.username_pw_set(self.mqtt_username, self.mqtt_password)
        return client

    def set_dc_port(self, enabled: bool) -> bool:
        """Enable or disable DC port on EcoFlow Delta 2."""
        if not HAS_MQTT:
            print("Error: paho-mqtt library not installed. Install with: pip install paho-mqtt", file=sys.stderr)
            return False

        try:
            client = self._create_client()
            if not client:
                return False

            client.connect(self.mqtt_host, self.mqtt_port, keepalive=10)

            # EcoFlow MQTT topic for DC port control
            # Set command payload: {"id": 1, "method": "setDcOutState", "params": {"enable": 1 or 0}}
            payload = {
                "id": 1,
                "method": "setDcOutState",
                "params": {"enable": 1 if enabled else 0}
            }

            topic = f"EF_SN{self.serial.upper()}/stan/set"
            client.publish(topic, json.dumps(payload))

            client.disconnect()
            state_str = "enabled" if enabled else "disabled"
            print(f"EcoFlow DC port {state_str} (sent to {self.serial})")
            return True
        except Exception as e:
            print(f"Error controlling EcoFlow DC port: {e}", file=sys.stderr)
            return False


class BatteryMonitor:
    """Main battery monitoring class with smart integrations."""

    def __init__(self, check_interval: int = DEFAULT_CHECK_INTERVAL,
                 enable_telegram: bool = True, enable_kasa: bool = True,
                 enable_ecoflow: bool = True):
        self.check_interval = check_interval
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_notification_state = None

        # Smart device integrations
        self.enable_telegram = enable_telegram and TELEGRAM_BOT_TOKEN and "..." not in TELEGRAM_BOT_TOKEN
        self.enable_kasa = enable_kasa and KASA_PLUG_IP
        self.enable_ecoflow = enable_ecoflow and ECOFLOW_SERIAL and ECOFLOW_MQTT_HOST

        if self.enable_telegram:
            self.telegram = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        if self.enable_kasa:
            self.kasa = KasaPlugController(KASA_PLUG_IP, KASA_PLUG_USERNAME, KASA_PLUG_PASSWORD)
        if self.enable_ecoflow:
            self.ecoflow = EcoFlowController(ECOFLOW_SERIAL, ECOFLOW_MQTT_HOST, ECOFLOW_MQTT_PORT,
                                            ECOFLOW_MQTT_USERNAME, ECOFLOW_MQTT_PASSWORD)

        # Ensure data directory exists
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._init_log_file()

    def _init_log_file(self) -> None:
        """Initialize CSV log file with headers if it doesn't exist."""
        if not LOG_FILE.exists():
            with open(LOG_FILE, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "battery_percent", "ac_power", "charging"])

    def get_battery_info(self) -> Optional[dict]:
        """Get battery information from pmset and ioreg."""
        try:
            result = subprocess.run(
                ["pmset", "-g", "batt"],
                capture_output=True,
                text=True,
                timeout=5
            )
            output = result.stdout.strip()

            if "no battery" in output.lower() or "internalbattery" not in output.lower():
                return None

            percent_match = re.search(r"(\d+)%", output)
            charging_match = re.search(r"(charging|discharging|finished)", output)
            ac_match = re.search(r"AC Power", output)

            if not percent_match:
                return None

            percent = int(percent_match.group(1))
            charging = charging_match.group(1) if charging_match else "unknown"
            ac_power = bool(ac_match)
            is_charging = ac_power and charging == "charging"

            return {
                "percent": percent,
                "ac_power": ac_power,
                "charging": is_charging,
                "raw_charging_state": charging
            }

        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError) as e:
            print(f"Error reading battery info: {e}", file=sys.stderr)
            return None

    def log_battery(self, info: dict) -> None:
        """Log battery info to CSV file."""
        try:
            with open(LOG_FILE, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now().isoformat(),
                    info["percent"],
                    info["ac_power"],
                    info["charging"]
                ])
        except Exception as e:
            print(f"Error logging battery data: {e}", file=sys.stderr)

    def send_notification(self, title: str, message: str, sound: bool = True) -> None:
        """Send macOS notification using osascript."""
        try:
            script = f'display notification "{message}" with title "{title}"'
            if sound:
                script += ' sound name "Glass"'
            subprocess.run(["osascript", "-e", script], check=False, timeout=5)
        except Exception as e:
            print(f"Error sending notification: {e}", file=sys.stderr)

    def check_and_notify(self, info: dict) -> None:
        """Check battery thresholds and send notifications and control smart devices."""
        percent = info["percent"]
        charging = info["charging"]

        if percent <= LOW_BATTERY_THRESHOLD and not charging:
            current_state = "low"
        elif percent >= HIGH_BATTERY_THRESHOLD and charging:
            current_state = "high"
        else:
            current_state = "normal"

        if current_state != self._last_notification_state:
            if current_state == "low":
                self.send_notification(
                    "Battery Low",
                    f"Battery at {percent}%. Please connect charger."
                )
                # Send Telegram alert
                if self.enable_telegram:
                    self.telegram.send_message(f"🔋 Battery Low: {percent}%\nPlease connect charger.")

                # Turn on Kasa plug to charge devices
                if self.enable_kasa:
                    try:
                        success = asyncio.run(self.kasa.turn_on())
                        if success:
                            print(f"Kasa plug turned ON (low battery)")
                        else:
                            print(f"Failed to turn ON Kasa plug after retries", file=sys.stderr)
                            self.send_notification(
                                "Kasa Plug Failed",
                                f"Could not turn on charging plug at {percent}% battery"
                            )
                    except Exception as e:
                        print(f"Error controlling Kasa plug: {e}", file=sys.stderr)

                # Enable EcoFlow DC port
                if self.enable_ecoflow:
                    self.ecoflow.set_dc_port(enabled=True)

            elif current_state == "high":
                self.send_notification(
                    "Battery Charged",
                    f"Battery at {percent}%. Consider unplugging to preserve battery health."
                )
                # Send Telegram alert
                if self.enable_telegram:
                    self.telegram.send_message(f"⚡ Battery Charged: {percent}%\nConsider unplugging.")

                # Turn off Kasa plug when charging is done
                if self.enable_kasa:
                    try:
                        success = asyncio.run(self.kasa.turn_off())
                        if success:
                            print(f"Kasa plug turned OFF (high battery)")
                        else:
                            print(f"Failed to turn OFF Kasa plug after retries", file=sys.stderr)
                            self.send_notification(
                                "Kasa Plug Failed",
                                f"Could not turn off charging plug at {percent}% battery"
                            )
                    except Exception as e:
                        print(f"Error controlling Kasa plug: {e}", file=sys.stderr)

                # Disable EcoFlow DC port
                if self.enable_ecoflow:
                    self.ecoflow.set_dc_port(enabled=False)

            self._last_notification_state = current_state

    def monitor_loop(self) -> None:
        """Main monitoring loop."""
        print(f"Battery monitor started (interval: {self.check_interval}s)")
        print(f"Logging to: {LOG_FILE}")
        print(f"Integrations: Telegram={self.enable_telegram}, Kasa={self.enable_kasa}, EcoFlow={self.enable_ecoflow}")
        print("Press Ctrl+C to stop")

        while not self._stop_event.is_set():
            info = self.get_battery_info()

            if info is None:
                print("No battery detected (desktop Mac or battery not present)")
            else:
                self.log_battery(info)
                self.check_and_notify(info)
                status = "charging" if info["charging"] else ("on AC" if info["ac_power"] else "on battery")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Battery: {info['percent']}% | {status}")

            self._stop_event.wait(self.check_interval)

        print("Battery monitor stopped")

    def start(self) -> bool:
        """Start the monitor in background thread."""
        if self.running:
            print("Monitor already running")
            return False

        if self.get_battery_info() is None:
            print("Warning: No battery detected. Monitor will run but may not provide useful data.")

        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self._thread.start()

        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))

        return True

    def stop(self) -> bool:
        """Stop the monitor."""
        if not self.running:
            print("Monitor not running")
            return False

        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.running = False

        if PID_FILE.exists():
            PID_FILE.unlink()

        return True

    def status(self) -> dict:
        """Get current status."""
        info = self.get_battery_info()
        return {
            "running": self.running,
            "check_interval": self.check_interval,
            "battery": info,
            "log_file": str(LOG_FILE),
            "pid_file": str(PID_FILE),
        }


def get_launch_agent_plist(script_path: Path, check_interval: int) -> dict:
    """Generate LaunchAgent plist configuration."""
    return {
        "Label": PLIST_NAME,
        "ProgramArguments": [
            sys.executable,
            str(script_path),
            "start",
            "--interval",
            str(check_interval)
        ],
        "RunAtLoad": True,
        "KeepAlive": {
            "SuccessfulExit": False,
            "Crashed": True
        },
        "StandardOutPath": str(DATA_DIR / "battery_monitor.out.log"),
        "StandardErrorPath": str(DATA_DIR / "battery_monitor.err.log"),
        "WorkingDirectory": str(SCRIPT_DIR),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin"
        }
    }


def install_launch_agent(script_path: Path, check_interval: int) -> bool:
    """Install LaunchAgent plist."""
    try:
        LAUNCH_AGENT_DIR.mkdir(parents=True, exist_ok=True)
        plist = get_launch_agent_plist(script_path, check_interval)
        with open(PLIST_FILE, "wb") as f:
            plistlib.dump(plist, f)
        subprocess.run(["launchctl", "load", str(PLIST_FILE)], check=True)
        print(f"LaunchAgent installed and loaded: {PLIST_FILE}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to load LaunchAgent: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error installing LaunchAgent: {e}", file=sys.stderr)
        return False


def uninstall_launch_agent() -> bool:
    """Uninstall LaunchAgent plist."""
    try:
        if PLIST_FILE.exists():
            subprocess.run(["launchctl", "unload", str(PLIST_FILE)], check=False)
            PLIST_FILE.unlink()
            print(f"LaunchAgent uninstalled: {PLIST_FILE}")
        else:
            print("LaunchAgent not installed")
        return True
    except Exception as e:
        print(f"Error uninstalling LaunchAgent: {e}", file=sys.stderr)
        return False


def launch_agent_status() -> dict:
    """Check LaunchAgent status."""
    installed = PLIST_FILE.exists()
    loaded = False

    if installed:
        try:
            result = subprocess.run(
                ["launchctl", "list", PLIST_NAME],
                capture_output=True,
                text=True
            )
            loaded = result.returncode == 0
        except Exception:
            pass

    return {"installed": installed, "loaded": loaded, "plist_path": str(PLIST_FILE)}


def save_config(check_interval: int) -> None:
    """Save configuration to file."""
    config = {"check_interval": check_interval}
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)


def load_config() -> int:
    """Load configuration from file."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                config = json.load(f)
                return config.get("check_interval", DEFAULT_CHECK_INTERVAL)
        except Exception:
            pass
    return DEFAULT_CHECK_INTERVAL


def run_daemon(check_interval: int) -> None:
    """Run monitor as daemon (for LaunchAgent)."""
    monitor = BatteryMonitor(check_interval=check_interval)

    def signal_handler(signum, frame):
        print(f"Received signal {signum}, stopping...")
        monitor.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    if not monitor.start():
        sys.exit(1)

    try:
        while monitor.running:
            time.sleep(1)
    except KeyboardInterrupt:
        monitor.stop()


def main():
    parser = argparse.ArgumentParser(
        description="macOS Battery Monitor - Monitor battery with smart integrations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  battery_monitor.py start                      # Start monitoring
  battery_monitor.py start --interval 30        # Start with 30s interval
  battery_monitor.py stop                       # Stop monitoring
  battery_monitor.py status                     # Show current status
  battery_monitor.py install                    # Install as LaunchAgent
  battery_monitor.py uninstall                  # Remove LaunchAgent
  battery_monitor.py action telegram            # Test Telegram notification
  battery_monitor.py action kasa-on             # Turn on Kasa plug
  battery_monitor.py action kasa-off            # Turn off Kasa plug
  battery_monitor.py action ecoflow-dc-on       # Enable EcoFlow DC port
  battery_monitor.py action ecoflow-dc-off      # Disable EcoFlow DC port
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    start_parser = subparsers.add_parser("start", help="Start battery monitoring")
    start_parser.add_argument(
        "--interval", "-i",
        type=int,
        default=None,
        help=f"Check interval in seconds (default: {DEFAULT_CHECK_INTERVAL})"
    )
    start_parser.add_argument(
        "--daemon", "-d",
        action="store_true",
        help="Run as daemon (for LaunchAgent use)"
    )

    subparsers.add_parser("stop", help="Stop battery monitoring")
    subparsers.add_parser("status", help="Show monitor status")

    install_parser = subparsers.add_parser("install", help="Install as LaunchAgent")
    install_parser.add_argument(
        "--interval", "-i",
        type=int,
        default=None,
        help=f"Check interval in seconds (default: {DEFAULT_CHECK_INTERVAL})"
    )

    subparsers.add_parser("uninstall", help="Remove LaunchAgent")

    action_parser = subparsers.add_parser("action", help="Perform manual actions")
    action_parser.add_argument(
        "action",
        choices=["telegram", "kasa-on", "kasa-off", "ecoflow-dc-on", "ecoflow-dc-off"],
        help="Action to perform"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if hasattr(args, 'interval') and args.interval is not None:
        check_interval = args.interval
    else:
        check_interval = load_config()

    script_path = Path(__file__).resolve()

    if args.command == "start":
        if args.daemon:
            run_daemon(check_interval)
        else:
            monitor = BatteryMonitor(check_interval=check_interval)
            save_config(check_interval)

            def signal_handler(signum, frame):
                print("\nStopping...")
                monitor.stop()
                sys.exit(0)

            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)

            monitor.start()
            try:
                while monitor.running:
                    time.sleep(1)
            except KeyboardInterrupt:
                monitor.stop()

    elif args.command == "stop":
        if PID_FILE.exists():
            try:
                with open(PID_FILE) as f:
                    pid = int(f.read().strip())
                os.kill(pid, signal.SIGTERM)
                print(f"Sent stop signal to PID {pid}")
            except (ValueError, ProcessLookupError, PermissionError) as e:
                print(f"Could not stop daemon: {e}")
        else:
            print("No PID file found. Is the monitor running?")

    elif args.command == "status":
        monitor = BatteryMonitor(check_interval=check_interval)
        status = monitor.status()
        agent_status = launch_agent_status()

        print("=== Battery Monitor Status ===")
        print(f"Running: {'Yes' if status['running'] else 'No'}")
        print(f"Check Interval: {status['check_interval']}s")
        print(f"Log File: {status['log_file']}")
        print(f"PID File: {status['pid_file']}")

        if status["battery"]:
            b = status["battery"]
            charging_str = "charging" if b["charging"] else ("on AC" if b["ac_power"] else "on battery")
            print(f"\nCurrent Battery: {b['percent']}% | {charging_str}")
        else:
            print("\nCurrent Battery: Not detected")

        print("\n=== LaunchAgent Status ===")
        print(f"Installed: {'Yes' if agent_status['installed'] else 'No'}")
        print(f"Loaded: {'Yes' if agent_status['loaded'] else 'No'}")
        print(f"Plist: {agent_status['plist_path']}")

    elif args.command == "install":
        save_config(check_interval)
        install_launch_agent(script_path, check_interval)

    elif args.command == "uninstall":
        uninstall_launch_agent()

    elif args.command == "action":
        action = args.action

        if action == "telegram":
            telegram = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
            if telegram.send_message("🧪 Test message from battery_monitor.py"):
                print("✓ Telegram notification sent successfully")
            else:
                print("✗ Failed to send Telegram notification")

        elif action == "kasa-on":
            if not KASA_PLUG_IP:
                print("Error: KASA_PLUG_IP not configured")
            else:
                kasa = KasaPlugController(KASA_PLUG_IP, KASA_PLUG_USERNAME, KASA_PLUG_PASSWORD)
                if asyncio.run(kasa.turn_on()):
                    print("✓ Kasa plug turned ON")
                else:
                    print("✗ Failed to turn on Kasa plug")

        elif action == "kasa-off":
            if not KASA_PLUG_IP:
                print("Error: KASA_PLUG_IP not configured")
            else:
                kasa = KasaPlugController(KASA_PLUG_IP, KASA_PLUG_USERNAME, KASA_PLUG_PASSWORD)
                if asyncio.run(kasa.turn_off()):
                    print("✓ Kasa plug turned OFF")
                else:
                    print("✗ Failed to turn off Kasa plug")

        elif action == "ecoflow-dc-on":
            if not ECOFLOW_SERIAL or not ECOFLOW_MQTT_HOST:
                print("Error: ECOFLOW_SERIAL and ECOFLOW_MQTT_HOST not configured")
            else:
                ecoflow = EcoFlowController(ECOFLOW_SERIAL, ECOFLOW_MQTT_HOST, ECOFLOW_MQTT_PORT,
                                          ECOFLOW_MQTT_USERNAME, ECOFLOW_MQTT_PASSWORD)
                if ecoflow.set_dc_port(enabled=True):
                    print("✓ EcoFlow DC port enabled")
                else:
                    print("✗ Failed to enable EcoFlow DC port")

        elif action == "ecoflow-dc-off":
            if not ECOFLOW_SERIAL or not ECOFLOW_MQTT_HOST:
                print("Error: ECOFLOW_SERIAL and ECOFLOW_MQTT_HOST not configured")
            else:
                ecoflow = EcoFlowController(ECOFLOW_SERIAL, ECOFLOW_MQTT_HOST, ECOFLOW_MQTT_PORT,
                                          ECOFLOW_MQTT_USERNAME, ECOFLOW_MQTT_PASSWORD)
                if ecoflow.set_dc_port(enabled=False):
                    print("✓ EcoFlow DC port disabled")
                else:
                    print("✗ Failed to disable EcoFlow DC port")


if __name__ == "__main__":
    main()
