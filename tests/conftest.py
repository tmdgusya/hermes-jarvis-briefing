"""Pytest sys.path setup for the jarvis-briefing plugin tests.

Plugins ship with a local ``clap_detector`` module (not a ``tools.``
path). Tests import it directly via a sys.path prepend so the plugin
is runnable in isolation, outside the Hermes plugin loader.
"""

import sys
import types
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Handler tests patch ``tools.voice_mode.detect_audio_environment`` and
# ``play_beep``. In a standalone plugin checkout Hermes itself may not be on
# PYTHONPATH, so provide a tiny stub package for patch resolution. The real
# Hermes runtime still supplies the actual module.
if "tools" not in sys.modules:
    sys.modules["tools"] = types.ModuleType("tools")
if "tools.voice_mode" not in sys.modules:
    voice_mode = types.ModuleType("tools.voice_mode")
    voice_mode.detect_audio_environment = lambda: {"available": False, "warnings": [], "notices": []}
    voice_mode.play_beep = lambda *args, **kwargs: None
    sys.modules["tools.voice_mode"] = voice_mode
    setattr(sys.modules["tools"], "voice_mode", voice_mode)
