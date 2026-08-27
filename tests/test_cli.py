from __future__ import annotations

import unittest

from typer.testing import CliRunner

from tracesurface.cli import app


class CliHelpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_scan_only_exposes_user_facing_concurrency(self) -> None:
        result = self.runner.invoke(app, ["scan", "--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("--sites", result.stdout)
        self.assertIn("--rate", result.stdout)
        self.assertIn("--cpu-workers", result.stdout)
        self.assertNotIn("--http-concurrency", result.stdout)
        self.assertNotIn("高级", result.stdout)

    def test_browser_install_is_managed_by_scan(self) -> None:
        result = self.runner.invoke(app, ["--help"])

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn("install-browser", result.stdout)


if __name__ == "__main__":
    unittest.main()
