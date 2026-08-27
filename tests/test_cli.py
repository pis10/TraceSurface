from __future__ import annotations

import re
import unittest

from typer.testing import CliRunner

from tracesurface.cli import app

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


class CliHelpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_scan_only_exposes_user_facing_concurrency(self) -> None:
        result = self.runner.invoke(app, ["scan", "--help"])
        stdout = _plain(result.stdout)

        self.assertEqual(result.exit_code, 0)
        self.assertIn("--sites", stdout)
        self.assertIn("--rate", stdout)
        self.assertIn("--cpu-workers", stdout)
        self.assertNotIn("--http-concurrency", stdout)
        self.assertNotIn("高级", stdout)

    def test_browser_install_is_managed_by_scan(self) -> None:
        result = self.runner.invoke(app, ["--help"])
        stdout = _plain(result.stdout)

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn("install-browser", stdout)


if __name__ == "__main__":
    unittest.main()
