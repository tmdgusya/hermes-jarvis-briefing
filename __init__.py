"""jarvis-briefing plugin — clap-triggered Korean morning briefing.

Registers a ``/jarvis`` slash command that:

1. Verifies audio is available.
2. Enables Hermes voice mode (+ TTS) if not already on.
3. Plays a ready beep, opens an ``sd.InputStream``, waits up to 30s for
   a double-clap via the local ``ClapAnalyzer`` state machine.
4. On success, plays a confirm beep and injects a Korean briefing prompt
   instructing the agent to call the bundled ``weather`` skill and read
   out today's tasks. The normal agent loop then handles skill
   invocation, LLM generation, and TTS playback (because ``_voice_tts``
   is set).

On timeout, reports the observed peak RMS so the user can tune
``CLAP_RMS_THRESHOLD`` via environment override or config.

All CLI state access goes through ``ctx._manager._cli_ref`` — the same
pattern ``PluginContext.inject_message`` uses internally.
"""

from __future__ import annotations

import logging
import os

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
# Briefing prompt
# ---------------------------------------------------------------------------

# Hardcoded for v0.1; future work parses ~/tasks/todo.md or pulls from
# the todo_tool. Set JARVIS_TODOS env var to override (newline-delimited).
_DEFAULT_TODOS = (
    "오전 10시 팀 스탠드업 미팅",
    "오후 2시 강의 녹화 세션",
    "저녁 운동 + 단백질 보충",
)


def _resolve_todos() -> tuple[str, ...]:
    raw = os.environ.get("JARVIS_TODOS", "").strip()
    if not raw:
        return _DEFAULT_TODOS
    parsed = tuple(line.strip() for line in raw.splitlines() if line.strip())
    return parsed or _DEFAULT_TODOS


def _build_briefing_prompt() -> str:
    """Construct the LLM prompt. Contains literal substrings the tests check."""
    todos = "\n".join(f"- {t}" for t in _resolve_todos())
    return (
        "지금 즉시 weather 스킬을 호출해 서울의 현재 날씨를 가져오고, "
        "아래 할 일 목록과 종합해서 한국어 아침 브리핑을 만들어.\n\n"
        "형식 엄수:\n"
        "- 한국어 3문장. 정확히 3문장.\n"
        "- 1문: 오늘 가장 놓치면 안 될 한 건을 리드로.\n"
        "- 2문: 날씨 + 즉각 행동(우산/겉옷/외출 타이밍 등).\n"
        "- 3문: 나머지 할 일 요약 — 많으면 '그 외 N개'로 압축.\n"
        "- 톤: 뉴스 앵커처럼 짧고 단정적으로. 이모지·불릿·마크다운 금지.\n"
        "- 답변 전체가 TTS로 낭독되므로 부가 설명, 머리말, 마무리 인사 금지. "
        "본문 3문장만 출력.\n\n"
        f"오늘 할 일:\n{todos}"
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
        cprint(f"{accent}박수 감지. 브리핑 준비 중…{rst}")

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
        description="Clap twice for a Korean morning briefing (weather + today's agenda).",
    )
    logger.info("jarvis-briefing plugin registered /jarvis command")
