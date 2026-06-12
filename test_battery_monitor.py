#!/usr/bin/env python3
"""Tests for battery_monitor. Run with: python3 -m unittest test_battery_monitor -v"""

import plistlib
import subprocess
import sys
import unittest
from pathlib import Path

import battery_monitor

REPO_DIR = Path(__file__).parent


class LaunchAgentPlistTests(unittest.TestCase):
    def test_generated_plist_path_includes_sbin_dirs(self):
        """launchd PATH must include /sbin and /usr/sbin so system binaries like ping resolve."""
        plist = battery_monitor.get_launch_agent_plist(Path("/tmp/battery_monitor.py"), 60)
        path_dirs = plist["EnvironmentVariables"]["PATH"].split(":")
        self.assertIn("/sbin", path_dirs)
        self.assertIn("/usr/sbin", path_dirs)

    def test_template_plist_path_includes_sbin_dirs(self):
        template = REPO_DIR / "com.user.batterymonitor.plist.template"
        with open(template, "rb") as f:
            plist = plistlib.load(f)
        path_dirs = plist["EnvironmentVariables"]["PATH"].split(":")
        self.assertIn("/sbin", path_dirs)
        self.assertIn("/usr/sbin", path_dirs)


class _FakeKasa:
    def __init__(self):
        self.turn_on_calls = 0
        self.turn_off_calls = 0
        self.succeed = False

    async def turn_on(self):
        self.turn_on_calls += 1
        return self.succeed

    async def turn_off(self):
        self.turn_off_calls += 1
        return self.succeed


class _FakeTelegram:
    def __init__(self):
        self.messages = []

    def send_message(self, text):
        self.messages.append(text)
        return True


def _make_monitor():
    mon = battery_monitor.BatteryMonitor(
        enable_telegram=False, enable_kasa=False, enable_ecoflow=False)
    mon.enable_kasa = True
    mon.kasa = _FakeKasa()
    mon.enable_telegram = True
    mon.telegram = _FakeTelegram()
    mon.send_notification = lambda *args, **kwargs: None
    return mon


LOW = {"percent": 15, "ac_power": False, "charging": False, "raw_charging_state": "discharging"}
HIGH = {"percent": 85, "ac_power": True, "charging": True, "raw_charging_state": "charging"}


class KasaFailureHandlingTests(unittest.TestCase):
    def test_failed_turn_on_retries_on_subsequent_ticks(self):
        """A failed low-battery turn-on must be retried every tick while still low."""
        mon = _make_monitor()
        mon.check_and_notify(LOW)
        mon.check_and_notify(LOW)
        self.assertEqual(mon.kasa.turn_on_calls, 2,
                         "second tick in low state must retry the failed turn-on")

    def test_retry_stops_after_success(self):
        mon = _make_monitor()
        mon.check_and_notify(LOW)            # fails
        mon.kasa.succeed = True
        mon.check_and_notify(LOW)            # retry succeeds
        mon.check_and_notify(LOW)            # no further attempts needed
        self.assertEqual(mon.kasa.turn_on_calls, 2)

    def test_failed_turn_on_sends_telegram_alert(self):
        """Turn-on failure must reach Telegram, not just the (unattended) Mac."""
        mon = _make_monitor()
        mon.check_and_notify(LOW)
        failure_alerts = [m for m in mon.telegram.messages
                          if "fail" in m.lower() or "could not" in m.lower()]
        self.assertTrue(failure_alerts,
                        f"no failure alert in telegram messages: {mon.telegram.messages}")

    def test_failed_turn_off_sends_telegram_alert(self):
        mon = _make_monitor()
        mon.check_and_notify(HIGH)
        failure_alerts = [m for m in mon.telegram.messages
                          if "fail" in m.lower() or "could not" in m.lower()]
        self.assertTrue(failure_alerts,
                        f"no failure alert in telegram messages: {mon.telegram.messages}")


class KasaImportTests(unittest.TestCase):
    def test_import_emits_no_smartplug_deprecation_warning(self):
        """Importing the module must not warn about the deprecated SmartPlug API."""
        result = subprocess.run(
            [sys.executable, "-W", "always::DeprecationWarning", "-c", "import battery_monitor"],
            capture_output=True, text=True, cwd=REPO_DIR, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("SmartPlug is deprecated", result.stderr)


if __name__ == "__main__":
    unittest.main()
