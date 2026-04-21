"""Unit tests for clap_detector.ClapAnalyzer (pure state machine).

Lives in the jarvis-briefing plugin. Imports the plugin-local module
via the sys.path prepend in ``conftest.py``.
"""

import unittest

import numpy as np

from clap_detector import ClapAnalyzer


def _silence(n_samples: int = 1600) -> np.ndarray:
    """100ms of near-silence at 16kHz (int16)."""
    return (np.random.randn(n_samples) * 30).astype(np.int16)


def _clap(n_samples: int = 800) -> np.ndarray:
    """50ms loud impulse at 16kHz (int16). RMS ~= 15000."""
    samples = (np.random.randn(n_samples) * 15000).astype(np.int16)
    return samples


class TestClapAnalyzerSingle(unittest.TestCase):
    def setUp(self):
        np.random.seed(0xC1AB)

    def test_quiet_input_does_not_trigger(self):
        a = ClapAnalyzer()
        now = 0.0
        for _ in range(10):
            self.assertFalse(a.process_chunk(_silence(), now))
            now += 0.1

    def test_single_clap_does_not_trigger(self):
        a = ClapAnalyzer()
        self.assertFalse(a.process_chunk(_silence(), 0.0))
        self.assertFalse(a.process_chunk(_clap(), 0.1))
        # Tail silence — still no second clap
        for i in range(5):
            self.assertFalse(a.process_chunk(_silence(), 0.2 + i * 0.1))


class TestClapAnalyzerDouble(unittest.TestCase):
    def setUp(self):
        np.random.seed(0xC1AB)

    def test_double_clap_within_window_triggers(self):
        a = ClapAnalyzer()
        self.assertFalse(a.process_chunk(_silence(), 0.0))
        self.assertFalse(a.process_chunk(_clap(), 0.1))    # first clap
        self.assertFalse(a.process_chunk(_silence(), 0.5))  # gap (above cooldown)
        # Second clap must return True
        triggered = a.process_chunk(_clap(), 0.8)
        self.assertTrue(triggered)

    def test_double_clap_outside_window_does_not_trigger(self):
        a = ClapAnalyzer(window_seconds=2.0)
        self.assertFalse(a.process_chunk(_clap(), 0.0))    # first clap
        # Silence for 3 seconds (exceeds window)
        for i in range(30):
            self.assertFalse(a.process_chunk(_silence(), 0.1 + i * 0.1))
        # A clap after window is treated as new "first clap", not a double
        self.assertFalse(a.process_chunk(_clap(), 3.5))

    def test_three_claps_trigger_once_then_rearm(self):
        """Three claps in sequence: fires once on 2nd, then re-arms.

        This pins current behavior. The /jarvis handler stops listening
        on the first True, so the re-arm only matters for long-running
        callers that reuse the analyzer.
        """
        a = ClapAnalyzer()
        self.assertFalse(a.process_chunk(_clap(), 0.0))  # 1st: arm
        self.assertTrue(a.process_chunk(_clap(), 0.5))    # 2nd: trigger
        # 3rd clap re-arms (fresh first clap), no trigger on its own
        self.assertFalse(a.process_chunk(_clap(), 1.0))
        # 4th clap would trigger a second pair — demonstrate re-arm worked
        self.assertTrue(a.process_chunk(_clap(), 1.5))


class TestClapAnalyzerCooldown(unittest.TestCase):
    def setUp(self):
        np.random.seed(0xC1AB)

    def test_rapid_repeat_within_cooldown_ignored(self):
        """Back-to-back chunks from the same clap shouldn't count as two."""
        a = ClapAnalyzer(cooldown_seconds=0.3)
        # Same clap spills across two consecutive chunks — only one event
        self.assertFalse(a.process_chunk(_clap(), 0.0))
        result = a.process_chunk(_clap(), 0.05)  # within cooldown
        self.assertFalse(result, "chunks within cooldown must not arm a second clap")


class TestClapAnalyzerReset(unittest.TestCase):
    def setUp(self):
        np.random.seed(0xC1AB)

    def test_reset_clears_armed_state(self):
        a = ClapAnalyzer()
        a.process_chunk(_clap(), 0.0)  # armed with first clap
        a.reset()
        # After reset, the next clap must be treated as a new first-clap
        self.assertFalse(a.process_chunk(_clap(), 0.1))


class TestClapAnalyzerPeakRms(unittest.TestCase):
    def setUp(self):
        np.random.seed(0xC1AB)

    def test_peak_rms_tracks_maximum_across_chunks(self):
        a = ClapAnalyzer()
        self.assertEqual(a.peak_rms, 0.0)

        # Silence only: peak becomes a small non-zero value (synthetic noise RMS ~30).
        a.process_chunk(_silence(), 0.0)
        self.assertLess(a.peak_rms, 100)

        # Clap arrives: peak jumps into the clap range.
        a.process_chunk(_clap(), 0.1)
        peak_after_clap = a.peak_rms
        self.assertGreater(peak_after_clap, 5000)

        # A quieter chunk afterwards must not lower the peak.
        a.process_chunk(_silence(), 0.2)
        self.assertEqual(a.peak_rms, peak_after_clap)

    def test_peak_rms_survives_reset(self):
        a = ClapAnalyzer()
        a.process_chunk(_clap(), 0.0)
        peak = a.peak_rms
        self.assertGreater(peak, 5000)
        a.reset()
        self.assertEqual(a.peak_rms, peak)


class TestClapAnalyzerEdgeCases(unittest.TestCase):
    def setUp(self):
        np.random.seed(0xC1AB)

    def test_zero_length_samples_returns_false_and_does_not_raise(self):
        a = ClapAnalyzer()
        empty = np.array([], dtype=np.int16)
        self.assertFalse(a.process_chunk(empty, 0.0))
        # State must be untouched — a real clap immediately after still arms cleanly
        self.assertFalse(a.process_chunk(_clap(), 0.1))  # first clap
        self.assertTrue(a.process_chunk(_clap(), 0.5))   # double clap fires


if __name__ == "__main__":
    unittest.main()
