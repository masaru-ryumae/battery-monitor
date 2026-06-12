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
