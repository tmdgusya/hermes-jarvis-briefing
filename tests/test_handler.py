"""Behavioral tests for the ``/jarvis`` plugin handler.

Instead of constructing a real ``PluginContext`` we build a
``SimpleNamespace`` facade with ``_manager._cli_ref`` and
``inject_message`` — the two hooks the handler actually uses.
"""

import importlib.util
import queue
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _load_plugin_module():
    """Load the plugin's ``__init__.py`` as a standalone module.

    Avoids going through Hermes's plugin loader so the test can run in
    isolation (e.g. CI without a full Hermes install).
    """
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
    # Mirror PluginContext.inject_message — route to cli's _pending_input
    # (or _interrupt_queue when agent is running).
    def _inject(content: str, role: str = "user") -> bool:
        msg = content if role == "user" else f"[{role}] {content}"
        if cli_stub._agent_running:
            cli_stub._interrupt_queue.put(msg)
        else:
            cli_stub._pending_input.put(msg)
        return True
    return SimpleNamespace(_manager=manager, inject_message=_inject)


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
        ) as MockDetector:
            MockDetector.return_value.listen.return_value = True
            self._call_handler(ctx)

        cli._enable_voice_mode.assert_called_once()
        MockDetector.return_value.listen.assert_called_once_with(timeout_seconds=30.0)
        self.assertTrue(cli._voice_tts, "TTS must be enabled so the briefing is spoken")
        self.assertGreaterEqual(mock_beep.call_count, 1)
        self.assertFalse(cli._pending_input.empty())
        queued = cli._pending_input.get_nowait()
        self.assertIn("weather", queued.lower())
        self.assertIn("할 일", queued)
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
            MockDetector.return_value.peak_rms = 500.0  # concrete value for formatting
            self._call_handler(ctx)
        self.assertTrue(cli._pending_input.empty(), "timeout must not inject a briefing prompt")

    def test_voice_mode_already_on_does_not_double_enable(self):
        cli = _make_cli_stub(voice_mode=True)  # already on
        ctx = _make_ctx(cli)
        with patch(
            "tools.voice_mode.detect_audio_environment",
            return_value={"available": True, "warnings": [], "notices": []},
        ), patch("tools.voice_mode.play_beep"), patch(
            f"{self.plugin.__name__}.ClapDetector"
        ) as MockDetector:
            MockDetector.return_value.listen.return_value = True
            self._call_handler(ctx)
        cli._enable_voice_mode.assert_not_called()
        self.assertTrue(cli._voice_tts)
        self.assertFalse(cli._pending_input.empty())


class TestBriefingPromptCustomization(unittest.TestCase):
    def setUp(self):
        self.plugin = _load_plugin_module()

    def test_default_todos_used_without_env(self):
        import os
        os.environ.pop("JARVIS_TODOS", None)
        prompt = self.plugin._build_briefing_prompt()
        self.assertIn("팀 스탠드업", prompt)
        self.assertIn("강의 녹화", prompt)

    def test_env_todos_override_default(self):
        import os
        os.environ["JARVIS_TODOS"] = "오전 9시 치과\n오후 4시 독서 모임"
        try:
            prompt = self.plugin._build_briefing_prompt()
            self.assertIn("치과", prompt)
            self.assertIn("독서 모임", prompt)
            self.assertNotIn("팀 스탠드업", prompt)
        finally:
            os.environ.pop("JARVIS_TODOS", None)


if __name__ == "__main__":
    unittest.main()
