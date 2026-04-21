"""jarvis-briefing plugin — clap-triggered Korean morning briefing.

Registers a ``/jarvis`` slash command that:

1. Verifies audio is available.
2. Enables Hermes voice mode (+ TTS) if not already on.
3. Plays a ready beep, opens an ``sd.InputStream``, waits up to 30s for
   a double-clap via the local ``ClapAnalyzer`` state machine.
4. On success, fetches today's Google Calendar events via the ``gws`` CLI
   and injects a Korean briefing prompt that instructs the agent to call
   the bundled ``weather`` skill, combining weather + today's agenda
   into a 3-sentence broadcast. The normal agent loop handles skill
   invocation, LLM generation, and TTS playback (because ``_voice_tts``
   is set).

On timeout, reports the observed peak RMS so the user can tune
``CLAP_RMS_THRESHOLD`` via config.

All CLI state access goes through ``ctx._manager._cli_ref`` — the same
pattern ``PluginContext.inject_message`` uses internally.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime
from typing import Any

try:
    # Normal load through Hermes's plugin system — module is a package.
    from . import clap_detector as _cd
    from .clap_detector import CLAP_RMS_THRESHOLD, ClapDetector
except ImportError:
    # Fallback for standalone / test execution where the plugin is
    # imported without a parent package (sys.path prepended by conftest).
    import clap_detector as _cd  # type: ignore[no-redef]
    from clap_detector import CLAP_RMS_THRESHOLD, ClapDetector  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Calendar fetching via gws CLI
# ---------------------------------------------------------------------------

GWS_TIMEOUT_SECONDS = 10.0


def _fetch_todays_events() -> list[dict[str, Any]] | None:
    """Call ``gws calendar +agenda --today --format json`` and return events.

    Returns a list of event dicts (each with ``summary``, ``start``, etc.)
    on success. Returns ``None`` if ``gws`` is missing, OAuth expired, or
    the command failed for any other reason — the caller should degrade
    gracefully rather than block the briefing.
    """
    if shutil.which("gws") is None:
        logger.info("gws CLI not found on PATH; skipping calendar fetch")
        return None

    try:
        result = subprocess.run(
            ["gws", "calendar", "+agenda", "--today", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=GWS_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("gws subprocess failed: %s", exc)
        return None

    if result.returncode != 0:
        logger.warning(
            "gws +agenda returned non-zero (code=%d); stderr=%s",
            result.returncode, (result.stderr or "")[:200],
        )
        return None

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("gws output was not valid JSON: %s", exc)
        return None

    events = payload.get("events")
    if not isinstance(events, list):
        logger.warning("gws output lacked an 'events' list: %r", payload)
        return None

    return events


def _format_events_for_prompt(events: list[dict[str, Any]] | None) -> str:
    """Render fetched events into a compact bullet list for the LLM.

    Falls back to explicit placeholder strings for the two degraded cases
    so the LLM never has to guess whether "no entry" means "empty schedule"
    or "fetch failed" — it can phrase the briefing accordingly.
    """
    if events is None:
        return "(캘린더 조회 실패 — gws CLI 또는 OAuth 상태 확인 필요)"
    if not events:
        return "오늘 등록된 일정 없음"

    lines: list[str] = []
    for ev in events:
        summary = ev.get("summary") or "(제목 없음)"
        start_raw = ev.get("start") or ""
        time_str = _extract_time_label(start_raw)
        lines.append(f"- {time_str} {summary}")
    return "\n".join(lines)


def _extract_time_label(start_raw: str) -> str:
    """Turn an ISO8601 ``start`` into a Korean-readable HH:MM label.

    - ``2026-04-22T19:00:00+09:00`` → ``19:00``
    - ``2026-04-22`` (all-day event) → ``종일``
    - anything unparseable → raw string truncated
    """
    if not start_raw:
        return "시간미상"
    if "T" not in start_raw:
        return "종일"
    try:
        # Python 3.11+: fromisoformat handles offsets natively.
        dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%H:%M")
    except ValueError:
        return start_raw[11:16] if len(start_raw) >= 16 else start_raw


def _build_briefing_prompt() -> str:
    """Assemble the LLM prompt. Test contract: MUST contain the literal
    substrings ``weather``, ``일정``, ``3문장``.
    """
    events = _fetch_todays_events()
    agenda = _format_events_for_prompt(events)
    return (
        "지금 즉시 weather 스킬을 호출해 서울의 현재 날씨를 가져오고, "
        "아래 오늘 일정과 종합해서 한국어 아침 브리핑을 만들어.\n\n"
        "형식 엄수:\n"
        "- 한국어 3문장. 정확히 3문장.\n"
        "- 1문: 오늘 가장 놓치면 안 될 한 건을 리드로 (첫 일정 또는 가장 중요한 약속).\n"
        "- 2문: 날씨 + 즉각 행동(우산/겉옷/외출 타이밍 등).\n"
        "- 3문: 나머지 일정 요약 — 많으면 '그 외 N개'로 압축. 일정이 없으면 '오늘은 비어 있으니 준비 시간으로 쓰기 좋다' 같은 자연스러운 마무리.\n"
        "- 톤: 뉴스 앵커처럼 짧고 단정적으로. 이모지·불릿·마크다운 금지.\n"
        "- 답변 전체가 TTS로 낭독되므로 부가 설명, 머리말, 마무리 인사 금지. "
        "본문 3문장만 출력.\n\n"
        f"오늘 일정:\n{agenda}"
    )


# ---------------------------------------------------------------------------
# Handler — runs inside a Hermes CLI session
# ---------------------------------------------------------------------------


def _cli(ctx) -> object | None:
    """Resolve the CLI instance from the plugin context.

    Mirrors the private access pattern used by ``PluginContext.inject_message``.
    Returns ``None`` when the plugin is loaded outside a CLI session
    (e.g. gateway mode).
    """
    return getattr(ctx._manager, "_cli_ref", None)


def make_handler(ctx):
    """Build the ``/jarvis`` handler closed over ``ctx``.

    Extracted as a factory so tests can construct it with a fake ctx
    carrying a ``SimpleNamespace`` CLI stub.
    """

    def _handler(raw_args: str) -> str | None:  # noqa: ARG001 — args reserved for future
        from tools.voice_mode import detect_audio_environment, play_beep

        cli = _cli(ctx)
        if cli is None:
            return "Jarvis requires an interactive CLI session (not available in gateway mode)."

        cprint = getattr(cli, "_cprint", None) or (lambda *a, **k: None)
        accent = getattr(cli, "_ACCENT", "") if hasattr(cli, "_ACCENT") else ""
        dim = getattr(cli, "_DIM", "") if hasattr(cli, "_DIM") else ""
        rst = getattr(cli, "_RST", "") if hasattr(cli, "_RST") else ""

        env = detect_audio_environment()
        if not env["available"]:
            cprint(f"\n{accent}Jarvis requires working audio:{rst}")
            for warning in env["warnings"]:
                cprint(f"  {dim}{warning}{rst}")
            return None

        # Voice mode: idempotent enable.
        if not getattr(cli, "_voice_mode", False):
            enable = getattr(cli, "_enable_voice_mode", None)
            if callable(enable):
                enable()
            if not getattr(cli, "_voice_mode", False):
                return None  # enable refused — already messaged by cli

        # TTS must be on so the briefing gets spoken.
        voice_lock = getattr(cli, "_voice_lock", None)
        if voice_lock is not None:
            with voice_lock:
                cli._voice_tts = True
        else:
            cli._voice_tts = True

        cprint(f"\n{accent}Jarvis listening. 박수 두 번으로 브리핑 시작 (30초 타임아웃).{rst}")
        play_beep(frequency=880, duration=0.12, count=1)

        detector = ClapDetector()
        try:
            got_clap = detector.listen(timeout_seconds=30.0)
        except Exception as exc:
            cprint(f"{dim}Jarvis 청취 실패: {exc}. 오디오 장치가 사용 중이거나 권한 문제일 수 있습니다.{rst}")
            return None
        if not got_clap:
            peak = detector.peak_rms
            cprint(f"{dim}박수를 감지하지 못했습니다. "
                   f"관측된 최대 RMS: {peak:.0f} (임계값 {CLAP_RMS_THRESHOLD}).{rst}")
            if peak < 50:
                cprint(
                    f"  {dim}RMS가 거의 0입니다 — 마이크 입력이 잡히지 않았을 가능성이 큽니다. "
                    f"macOS 시스템 설정 → 개인정보 보호 → 마이크에서 Terminal(또는 iTerm) 권한을 확인하세요.{rst}"
                )
            elif peak < CLAP_RMS_THRESHOLD:
                suggested = max(int(peak * 0.7), 200)
                cprint(
                    f"  {dim}마이크가 임계값에 못 미쳤습니다. "
                    f"플러그인 설치 디렉토리의 clap_detector.py 에서 "
                    f"CLAP_RMS_THRESHOLD 를 {suggested} 부근으로 낮춰보세요.{rst}"
                )
            else:
                cprint(
                    f"  {dim}임계값은 넘었으나 더블 박수 패턴이 성립하지 않았습니다. "
                    f"박수 사이 간격을 0.3~3초 사이로 맞춰 다시 시도해주세요.{rst}"
                )
            return None

        play_beep(frequency=1320, duration=0.10, count=2)
        cprint(f"{accent}박수 감지. 캘린더 조회 후 브리핑 준비 중…{rst}")

        prompt = _build_briefing_prompt()
        ctx.inject_message(prompt, role="user")
        return None

    return _handler


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Plugin entry point. Wires the ``/jarvis`` slash command."""
    ctx.register_command(
        "jarvis",
        make_handler(ctx),
        description="Clap twice for a Korean morning briefing (weather + Google Calendar agenda).",
    )
    logger.info("jarvis-briefing plugin registered /jarvis command")
