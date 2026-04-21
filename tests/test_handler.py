"""Behavioral tests for the ``/jarvis`` plugin handler.

Instead of constructing a real ``PluginContext`` we build a
``SimpleNamespace`` facade with ``_manager._cli_ref`` and
``inject_message`` — the two hooks the handler actually uses.
"""

import importlib.util
import json
import queue
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _load_plugin_module():
    """Load the plugin's ``__init__.py`` as a standalone module."""
    module_name = "jarvis_briefing_under_test"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _make_cli_stub(voice_mode: bool = False) -> SimpleNamespace:
    stub = SimpleNamespace(
        _voice_mode=voice_mode,
        _voice_tts=False,
        _voice_lock=threading.Lock(),
        _pending_input=queue.Queue(),
        _interrupt_queue=queue.Queue(),
        _agent_running=False,
        _enable_voice_mode=MagicMock(),
        _cprint=MagicMock(),
    )

    def _fake_enable():
        stub._voice_mode = True

    stub._enable_voice_mode.side_effect = _fake_enable
    return stub


def _make_ctx(cli_stub) -> SimpleNamespace:
    manager = SimpleNamespace(_cli_ref=cli_stub)
    def _inject(content: str, role: str = "user") -> bool:
        msg = content if role == "user" else f"[{role}] {content}"
        if cli_stub._agent_running:
            cli_stub._interrupt_queue.put(msg)
        else:
            cli_stub._pending_input.put(msg)
        return True
    return SimpleNamespace(_manager=manager, inject_message=_inject)


# ---------------------------------------------------------------------------
# Handler orchestration tests
# ---------------------------------------------------------------------------

class TestJarvisHandler(unittest.TestCase):
    def setUp(self):
        self.plugin = _load_plugin_module()

    def _call_handler(self, ctx, raw_args: str = ""):
        return self.plugin.make_handler(ctx)(raw_args)

    def test_audio_unavailable_aborts_without_enabling_voice(self):
        cli = _make_cli_stub(voice_mode=False)
        ctx = _make_ctx(cli)
        with patch(
            "tools.voice_mode.detect_audio_environment",
            return_value={"available": False, "warnings": ["no microphone"], "notices": []},
        ):
            self._call_handler(ctx)
        cli._enable_voice_mode.assert_not_called()
        self.assertTrue(cli._pending_input.empty())

    def test_happy_path_enables_voice_tts_and_injects_briefing(self):
        cli = _make_cli_stub(voice_mode=False)
        ctx = _make_ctx(cli)
        with patch(
            "tools.voice_mode.detect_audio_environment",
            return_value={"available": True, "warnings": [], "notices": []},
        ), patch("tools.voice_mode.play_beep") as mock_beep, patch(
            f"{self.plugin.__name__}.ClapDetector"
        ) as MockDetector, patch(
            f"{self.plugin.__name__}._fetch_todays_events", return_value=[]
        ):
            MockDetector.return_value.listen.return_value = True
            self._call_handler(ctx)

        cli._enable_voice_mode.assert_called_once()
        MockDetector.return_value.listen.assert_called_once_with(timeout_seconds=30.0)
        self.assertTrue(cli._voice_tts, "TTS must be enabled so the briefing is spoken")
        self.assertGreaterEqual(mock_beep.call_count, 1)
        self.assertFalse(cli._pending_input.empty())
        queued = cli._pending_input.get_nowait()
        self.assertIn("weather", queued.lower())
        self.assertIn("일정", queued)
        self.assertIn("3문장", queued)

    def test_clap_timeout_does_not_inject_briefing(self):
        cli = _make_cli_stub(voice_mode=False)
        ctx = _make_ctx(cli)
        with patch(
            "tools.voice_mode.detect_audio_environment",
            return_value={"available": True, "warnings": [], "notices": []},
        ), patch("tools.voice_mode.play_beep"), patch(
            f"{self.plugin.__name__}.ClapDetector"
        ) as MockDetector:
            MockDetector.return_value.listen.return_value = False  # timeout
            MockDetector.return_value.peak_rms = 500.0
            self._call_handler(ctx)
        self.assertTrue(cli._pending_input.empty(), "timeout must not inject a briefing prompt")

    def test_voice_mode_already_on_does_not_double_enable(self):
        cli = _make_cli_stub(voice_mode=True)
        ctx = _make_ctx(cli)
        with patch(
            "tools.voice_mode.detect_audio_environment",
            return_value={"available": True, "warnings": [], "notices": []},
        ), patch("tools.voice_mode.play_beep"), patch(
            f"{self.plugin.__name__}.ClapDetector"
        ) as MockDetector, patch(
            f"{self.plugin.__name__}._fetch_todays_events", return_value=[]
        ):
            MockDetector.return_value.listen.return_value = True
            self._call_handler(ctx)
        cli._enable_voice_mode.assert_not_called()
        self.assertTrue(cli._voice_tts)
        self.assertFalse(cli._pending_input.empty())


# ---------------------------------------------------------------------------
# Calendar fetch tests
# ---------------------------------------------------------------------------

_SAMPLE_GWS_OUTPUT = json.dumps({
    "count": 2,
    "events": [
        {
            "calendar": "me@example.com",
            "end": "2026-04-22T11:00:00+09:00",
            "location": "",
            "start": "2026-04-22T10:00:00+09:00",
            "summary": "팀 스탠드업",
        },
        {
            "calendar": "me@example.com",
            "end": "2026-04-22T15:00:00+09:00",
            "location": "",
            "start": "2026-04-22T14:00:00+09:00",
            "summary": "강의 녹화",
        },
    ],
    "timeMax": "2026-04-23T00:00:00+09:00",
    "timeMin": "2026-04-22T00:00:00+09:00",
})


class TestCalendarFetch(unittest.TestCase):
    def setUp(self):
        self.plugin = _load_plugin_module()

    def test_fetch_parses_gws_output(self):
        with patch(f"{self.plugin.__name__}.shutil.which", return_value="/opt/homebrew/bin/gws"), \
             patch(f"{self.plugin.__name__}.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=0, stdout=_SAMPLE_GWS_OUTPUT, stderr="")
            events = self.plugin._fetch_todays_events()
        self.assertIsNotNone(events)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["summary"], "팀 스탠드업")
        self.assertEqual(events[1]["summary"], "강의 녹화")

    def test_fetch_returns_none_when_gws_missing(self):
        with patch(f"{self.plugin.__name__}.shutil.which", return_value=None):
            events = self.plugin._fetch_todays_events()
        self.assertIsNone(events)

    def test_fetch_returns_none_on_gws_nonzero_exit(self):
        with patch(f"{self.plugin.__name__}.shutil.which", return_value="/opt/homebrew/bin/gws"), \
             patch(f"{self.plugin.__name__}.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="auth error")
            events = self.plugin._fetch_todays_events()
        self.assertIsNone(events)

    def test_fetch_returns_none_on_invalid_json(self):
        with patch(f"{self.plugin.__name__}.shutil.which", return_value="/opt/homebrew/bin/gws"), \
             patch(f"{self.plugin.__name__}.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=0, stdout="not json", stderr="")
            events = self.plugin._fetch_todays_events()
        self.assertIsNone(events)

    def test_fetch_returns_none_on_timeout(self):
        import subprocess as sp
        with patch(f"{self.plugin.__name__}.shutil.which", return_value="/opt/homebrew/bin/gws"), \
             patch(f"{self.plugin.__name__}.subprocess.run",
                   side_effect=sp.TimeoutExpired("gws", 10)):
            events = self.plugin._fetch_todays_events()
        self.assertIsNone(events)


class TestPromptComposition(unittest.TestCase):
    def setUp(self):
        self.plugin = _load_plugin_module()

    def test_format_events_with_items(self):
        events = json.loads(_SAMPLE_GWS_OUTPUT)["events"]
        rendered = self.plugin._format_events_for_prompt(events)
        self.assertIn("10:00 팀 스탠드업", rendered)
        self.assertIn("14:00 강의 녹화", rendered)

    def test_format_events_empty(self):
        rendered = self.plugin._format_events_for_prompt([])
        self.assertEqual(rendered, "오늘 등록된 일정 없음")

    def test_format_events_fetch_failed(self):
        rendered = self.plugin._format_events_for_prompt(None)
        self.assertIn("조회 실패", rendered)

    def test_format_all_day_event(self):
        events = [{"summary": "워크숍", "start": "2026-04-22"}]
        rendered = self.plugin._format_events_for_prompt(events)
        self.assertIn("종일 워크숍", rendered)

    def test_prompt_includes_events_when_fetch_succeeds(self):
        events = json.loads(_SAMPLE_GWS_OUTPUT)["events"]
        with patch(f"{self.plugin.__name__}._fetch_todays_events", return_value=events):
            prompt = self.plugin._build_briefing_prompt()
        self.assertIn("팀 스탠드업", prompt)
        self.assertIn("강의 녹화", prompt)
        self.assertIn("weather", prompt.lower())
        self.assertIn("3문장", prompt)

    def test_prompt_degrades_gracefully_when_fetch_fails(self):
        with patch(f"{self.plugin.__name__}._fetch_todays_events", return_value=None):
            prompt = self.plugin._build_briefing_prompt()
        self.assertIn("조회 실패", prompt)
        # Prompt still valid — LLM gets to generate a weather-only briefing
        self.assertIn("weather", prompt.lower())
        self.assertIn("3문장", prompt)


if __name__ == "__main__":
    unittest.main()
