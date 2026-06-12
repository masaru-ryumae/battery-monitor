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
        self.turn_on_kwargs = []
        self.succeed = False
        self.raise_error = None

    async def turn_on(self, **kwargs):
        self.turn_on_calls += 1
        self.turn_on_kwargs.append(kwargs)
        if self.raise_error:
            raise self.raise_error
        return self.succeed

    async def turn_off(self, **kwargs):
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

    def test_exception_during_turn_on_alerts_and_retries(self):
        """A protocol exception (e.g. KasaException) must alert Telegram and arm the retry."""
        mon = _make_monitor()
        mon.kasa.raise_error = RuntimeError("protocol error")
        mon.check_and_notify(LOW)
        failure_alerts = [m for m in mon.telegram.messages
                          if "fail" in m.lower() or "error" in m.lower()]
        self.assertTrue(failure_alerts,
                        f"no failure alert in telegram messages: {mon.telegram.messages}")
        mon.kasa.raise_error = None
        mon.check_and_notify(LOW)
        self.assertEqual(mon.kasa.turn_on_calls, 2, "exception must arm per-tick retry")

    def test_retry_tick_uses_quick_attempt(self):
        """Per-tick retries must use the bounded quick mode, not the full 300s engine."""
        mon = _make_monitor()
        mon.check_and_notify(LOW)
        mon.check_and_notify(LOW)
        self.assertEqual(mon.kasa.turn_on_kwargs[0], {}, "initial attempt uses full retry budget")
        self.assertEqual(mon.kasa.turn_on_kwargs[1], {"quick": True})

    def test_no_telegram_spam_on_repeated_retry_failures(self):
        mon = _make_monitor()
        for _ in range(4):
            mon.check_and_notify(LOW)
        failure_alerts = [m for m in mon.telegram.messages
                          if "fail" in m.lower() or "could not" in m.lower()]
        self.assertEqual(len(failure_alerts), 1, "failure alert must fire once, not per tick")

    def test_retry_success_sends_confirmation(self):
        mon = _make_monitor()
        mon.check_and_notify(LOW)
        mon.kasa.succeed = True
        mon.check_and_notify(LOW)
        self.assertTrue(any("turned on" in m.lower() for m in mon.telegram.messages),
                        f"no success confirmation: {mon.telegram.messages}")

    def test_failed_turn_off_sends_telegram_alert(self):
        mon = _make_monitor()
        mon.check_and_notify(HIGH)
        failure_alerts = [m for m in mon.telegram.messages
                          if "fail" in m.lower() or "could not" in m.lower()]
        self.assertTrue(failure_alerts,
                        f"no failure alert in telegram messages: {mon.telegram.messages}")


class KasaQuickRetryTests(unittest.TestCase):
    def test_quick_mode_bounds_attempts(self):
        """quick=True must make at most 2 attempts and skip the multi-phase engine."""
        import asyncio
        ctrl = battery_monitor.KasaPlugController("192.0.2.1", phase1_base_delay=0.01)
        attempts = []

        async def always_fails():
            attempts.append(1)
            raise ConnectionError("unreachable")

        result = asyncio.run(ctrl._execute_with_retry(always_fails, quick=True))
        self.assertFalse(result)
        self.assertLessEqual(len(attempts), 2)


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
