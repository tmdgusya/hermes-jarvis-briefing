"""Clap detection for the /jarvis slash command.

Two layers:

1. ``ClapAnalyzer`` — pure-Python state machine. Given int16 audio chunks
   and a monotonic timestamp, it reports when a double-clap pattern
   (two short impulsive events within ``window_seconds``, separated by at
   least ``cooldown_seconds``) completes. No sounddevice dependency, so
   unit-testable with synthetic numpy arrays.

2. ``ClapDetector.listen()`` — thin sounddevice.InputStream adapter that
   feeds chunks to the analyzer and blocks until a double-clap is
   detected or a timeout expires. Added in Task 2.

Tunables are module constants; override via ``ClapAnalyzer(**kwargs)``
when tuning for a noisier environment.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables — chosen so that:
#   * Speech/ambient RMS (0-2000) does NOT trigger.
#   * A clap's brief peak (15k-30k on int16) clearly exceeds threshold.
#   * Two claps must be >=cooldown apart and <=window apart.
# ---------------------------------------------------------------------------
CLAP_RMS_THRESHOLD = 1200          # int16 RMS; must exceed this for a chunk to count as impulse.
                                   # Field-tuned on a MacBook built-in mic: 3000 never caught, 1500 caught
                                   # but barely, 1200 leaves headroom without touching typical speech
                                   # RMS (~200-800). Tune via peak_rms diagnostic on timeout.
CLAP_WINDOW_SECONDS = 3.0          # second clap must arrive within this
CLAP_COOLDOWN_SECONDS = 0.3        # min gap between two claps — also rejects one clap spread over chunks


class ClapAnalyzer:
    """Pure state machine; no hardware I/O.

    States::

        IDLE        -- waiting for first clap
        ARMED       -- saw first clap; waiting for second within window

    ``process_chunk`` advances the machine and returns ``True`` exactly
    once per completed double-clap, then resets to IDLE. If a caller
    keeps feeding chunks after a trigger, subsequent impulses re-arm
    as a fresh first-clap — i.e. three rapid claps trigger once (on
    the 2nd) and leave the 3rd armed. The ``/jarvis`` handler stops
    listening on the first ``True``, so this only matters for
    long-running reuse.
    """

    def __init__(
        self,
        rms_threshold: int = CLAP_RMS_THRESHOLD,
        window_seconds: float = CLAP_WINDOW_SECONDS,
        cooldown_seconds: float = CLAP_COOLDOWN_SECONDS,
    ) -> None:
        self._rms_threshold = int(rms_threshold)
        self._window = float(window_seconds)
        self._cooldown = float(cooldown_seconds)
        self._first_clap_at: Optional[float] = None
        self._last_impulse_at: Optional[float] = None
        self._peak_rms: float = 0.0

    @property
    def peak_rms(self) -> float:
        """Highest RMS observed since construction. Survives ``reset()``.

        Used for post-session diagnostics: if a listen session times out,
        the handler reports ``peak_rms`` so the user can tell whether the
        environment actually reached the threshold.
        """
        return self._peak_rms

    def reset(self) -> None:
        """Clear armed state; next impulse becomes a fresh first-clap.

        ``peak_rms`` is intentionally NOT reset — diagnostics span the full
        lifetime of the analyzer.
        """
        self._first_clap_at = None
        self._last_impulse_at = None

    def process_chunk(self, samples: np.ndarray, now: float) -> bool:
        """Feed one audio chunk.

        Args:
            samples: int16 1-D numpy array, typically 50-200ms at 16kHz.
            now: Monotonic timestamp in seconds.

        Returns:
            ``True`` exactly when the double-clap pattern completes on this
            chunk. ``False`` otherwise.
        """
        if samples.size == 0:
            return False

        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))

        # Track highest RMS for diagnostics, even for sub-threshold chunks.
        if rms > self._peak_rms:
            self._peak_rms = rms

        # Expire an old first-clap outside the window so that a fresh clap
        # starts a brand-new pair.
        if self._first_clap_at is not None and now - self._first_clap_at > self._window:
            logger.debug("clap: window expired; resetting")
            self.reset()

        if rms < self._rms_threshold:
            return False

        # Cooldown rejects the tail of the same clap spilling into the next chunk.
        if self._last_impulse_at is not None and now - self._last_impulse_at < self._cooldown:
            return False

        self._last_impulse_at = now

        if self._first_clap_at is None:
            self._first_clap_at = now
            logger.debug("clap: first impulse armed at %.3f", now)
            return False

        # Second impulse inside window + past cooldown — double-clap complete.
        logger.info("clap: double-clap detected (gap=%.2fs)", now - self._first_clap_at)
        self.reset()
        return True


# ---------------------------------------------------------------------------
# sounddevice adapter
# ---------------------------------------------------------------------------


def _import_sd():
    """Lazy-import sounddevice to avoid crashing in headless environments."""
    import sounddevice as sd  # type: ignore
    return sd


class ClapDetector:
    """Blocking double-clap listener wrapping sounddevice.InputStream.

    Intended for a **single call per process lifetime**::

        detector = ClapDetector()
        if detector.listen(timeout_seconds=30.0):
            # double clap detected
            ...
        else:
            # timed out before double clap
            ...

    Uses the same 16kHz / mono / int16 convention as ``tools.voice_mode``.

    ⚠ macOS limitation: sounddevice's ``InputStream`` can hang on
    close-then-reopen (see the note in ``tools/voice_mode.py``'s
    ``AudioRecorder._ensure_stream``). This class opens and closes a
    fresh stream on every ``listen()`` call, so invoking it repeatedly
    in the same process, or using it alongside an active voice-mode
    recording, may stall. The ``/jarvis`` handler only calls it once per
    session, which avoids the issue in normal use.
    """

    # Match the AudioRecorder convention in tools/voice_mode.py
    SAMPLE_RATE = 16000
    CHANNELS = 1
    DTYPE = "int16"
    # Chunk size trades latency (shorter = faster reaction) against CPU overhead.
    # 1600 samples @ 16kHz == 100ms, which is well below a clap's duration.
    BLOCKSIZE = 1600

    def __init__(self) -> None:
        self._analyzer = ClapAnalyzer()

    @property
    def peak_rms(self) -> float:
        """Highest RMS seen since this detector was constructed.

        Useful for the ``/jarvis`` timeout path: reporting the peak gives
        the user concrete data for tuning ``CLAP_RMS_THRESHOLD``.
        """
        return self._analyzer.peak_rms

    def listen(self, timeout_seconds: float = 30.0) -> bool:
        """Block until a double-clap is detected or timeout elapses.

        Returns:
            ``True`` if a double-clap fired; ``False`` on timeout.
        """
        sd = _import_sd()

        detected = threading.Event()

        def _callback(indata, frames, time_info, status):  # noqa: ARG001
            if status:
                logger.debug("sounddevice status: %s", status)
            if detected.is_set():
                return
            try:
                # indata is shape (frames, channels); flatten to 1-D int16.
                chunk = np.asarray(indata, dtype=np.int16).reshape(-1)
                if self._analyzer.process_chunk(chunk, time.monotonic()):
                    detected.set()
            except Exception:
                # Aborting (set) is preferred to hanging for the full timeout.
                logger.exception("clap analyzer raised; aborting listen()")
                detected.set()

        stream = sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=self.CHANNELS,
            dtype=self.DTYPE,
            blocksize=self.BLOCKSIZE,
            callback=_callback,
        )
        stream.start()
        try:
            return detected.wait(timeout=timeout_seconds)
        finally:
            try:
                stream.stop()
            except Exception:
                logger.debug("stream.stop() failed; continuing", exc_info=True)
            try:
                stream.close()
            except Exception:
                logger.debug("stream.close() failed; continuing", exc_info=True)
