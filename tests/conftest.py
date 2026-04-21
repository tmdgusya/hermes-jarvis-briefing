"""Pytest sys.path setup for the jarvis-briefing plugin tests.

Plugins ship with a local ``clap_detector`` module (not a ``tools.``
path). Tests import it directly via a sys.path prepend so the plugin
is runnable in isolation, outside the Hermes plugin loader.
"""

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
