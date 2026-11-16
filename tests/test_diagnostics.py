import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from config import AppConfig
from diagnostics import PROBE_PREFIX, run_probe, safe_configuration, write_report


class DiagnosticTests(unittest.TestCase):
    def test_configuration_report_omits_credentials_and_glossary_text(self):
        config = AppConfig(initial_prompt="Secret project terms", groq_api_key="private-key")
        report = safe_configuration(config)
        encoded = json.dumps(report)
        self.assertTrue(report["glossary_configured"])
        self.assertTrue(report["groq_key_configured"])
        self.assertNotIn("Secret project", encoded)
        self.assertNotIn("private-key", encoded)

    def test_native_probe_report_survives_stdout_noise(self):
        output = "native startup log\n" + PROBE_PREFIX + '{"status":"ok","cuda_device_count":1}\n'
        completed = SimpleNamespace(returncode=0, stdout=output, stderr="")
        with patch("diagnostics.subprocess.run", return_value=completed) as run:
            report = run_probe("cuda")
        self.assertEqual(report["cuda_device_count"], 1)
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(run.call_args.kwargs["timeout"], 20)

    def test_native_abort_and_timeout_become_reports(self):
        completed = SimpleNamespace(returncode=-6, stdout="", stderr="private machine details")
        with patch("diagnostics.subprocess.run", return_value=completed):
            report = run_probe("silero")
        self.assertEqual(report["exit_code"], -6)
        self.assertNotIn("private machine details", json.dumps(report))
        with patch("diagnostics.subprocess.run", side_effect=subprocess.TimeoutExpired("probe", 20)):
            self.assertTrue(run_probe("audio")["timed_out"])

    def test_invalid_probe_output_and_unknown_probe_are_rejected(self):
        completed = SimpleNamespace(returncode=0, stdout=PROBE_PREFIX + "[]", stderr="")
        with patch("diagnostics.subprocess.run", return_value=completed):
            self.assertEqual(run_probe("cuda")["status"], "error")
        with self.assertRaises(ValueError):
            run_probe("unexpected-command")

    def test_report_is_atomically_replaced_with_valid_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "diagnostics.json"
            path.write_text("old version", encoding="utf-8")
            write_report(str(path), {"status": "ok", "message": "नमस्ते"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["message"], "नमस्ते")
            self.assertEqual(list(Path(folder).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
