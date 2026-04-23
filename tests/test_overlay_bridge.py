"""Tests for the Jarvis overlay status bridge."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import overlay_bridge


class TestOverlayBridge(unittest.TestCase):
    def test_write_status_creates_status_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_path = Path(tmp) / "jarvis-overlay" / "status.json"
            with patch.object(overlay_bridge, "STATUS_FILE", status_path):
                written = overlay_bridge.write_status("listening")

            self.assertEqual(written, status_path)
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "listening")
            self.assertEqual(payload["label"], "듣는 중")
            self.assertIn("updated_at", payload)

    def test_write_status_rejects_unknown_state(self):
        with self.assertRaises(ValueError):
            overlay_bridge.write_status("unknown")


if __name__ == "__main__":
    unittest.main()
