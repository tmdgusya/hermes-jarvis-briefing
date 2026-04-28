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
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    # Normal load through Hermes's plugin system — module is a package.
    from . import clap_detector as _cd
    from .clap_detector import CLAP_RMS_THRESHOLD, ClapDetector
    from .overlay_bridge import write_status
except ImportError:
    # Fallback for standalone / test execution where the plugin is
    # imported without a parent package (sys.path prepended by conftest).
    import clap_detector as _cd  # type: ignore[no-redef]
    from clap_detector import CLAP_RMS_THRESHOLD, ClapDetector  # type: ignore[no-redef]
    from overlay_bridge import write_status  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

_OVERLAY_PORT = 8765
_OVERLAY_ROOT = Path.home() / ".hermes"
_OVERLAY_URL = f"http://127.0.0.1:{_OVERLAY_PORT}/plugins/jarvis-briefing/webview/"
_OVERLAY_BROWSER_OPENED = False


# ---------------------------------------------------------------------------
# Calendar fetching via gws CLI
# ---------------------------------------------------------------------------

GWS_TIMEOUT_SECONDS = 10.0

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

# Range metadata — maps the ``/jarvis <arg>`` user-facing keyword to the
# ``gws calendar +agenda`` flag plus Korean labels used in the prompt.
# Adding a new range (e.g. "days3") = add a row here, nothing else needed.
_RANGE_META: dict[str, dict[str, str]] = {
    "today": {
        "gws_flag": "--today",
        "label": "오늘",
        "empty_msg": "오늘 등록된 일정 없음",
    },
    "tomorrow": {
        "gws_flag": "--tomorrow",
        "label": "내일",
        "empty_msg": "내일 등록된 일정 없음",
    },
    "week": {
        "gws_flag": "--week",
        "label": "이번주",
        "empty_msg": "이번주 등록된 일정 없음",
    },
}

_DEMO_MODE_ALIASES = {
    "demo",
    "threads",
    "thread",
    "vibe",
    "vibecoding",
    "바이브코딩",
}

_DEMO_PRIMER_USER = """[JARVIS_DEMO_SCRIPT]
이 세션은 JARVIS 데모 촬영 모드다. 사용자는 박수 2번으로 Jarvis를 깨운 뒤 음성으로 여러 턴을 대화한다.
데모 안정성이 최우선이므로 실제 Threads API/웹 검색/도구 호출을 하지 말고 아래 스크립트 데이터를 그대로 사용한다.
항상 한국어로, 실제 비서처럼 짧고 자연스럽게 말한다. 한 답변은 보통 2~4문장으로 유지한다. 마크다운 표나 코드블록은 쓰지 않는다.

대화 흐름 목표:
1) 사용자가 Threads #바이브코딩 인기글 브리핑을 요청한다.
2) Jarvis가 Top 3를 말한다.
3) 사용자가 공통점/왜 인기인지 묻는다.
4) Jarvis가 패턴을 설명한다.
5) 사용자가 "그러면 내가 뭘 올리면 좋을까", "콘텐츠 아이디어 줘", "내 계정에 적용하면?" 같은 후속 질문을 한다.
6) Jarvis가 바로 실행 가능한 게시글 아이디어와 훅을 제안한다.
7) 사용자가 "하나 골라줘", "캡션까지 써줘", "오늘 올릴 걸로 정리해줘"라고 하면 Jarvis가 최종 게시글 초안을 짧게 만들어준다.

스크립트 데이터:
- 1위: "기획자가 혼자 앱 만든 후기" / 좋아요 2.4천 / 댓글 318 / 포인트: 비개발자가 AI로 MVP를 만들고 출시까지 갔다는 서사.
- 2위: "개발자 없이 창업한 이야기" / 좋아요 1.8천 / 댓글 207 / 포인트: 자동화와 노코드로 고객 결제까지 받은 사례.
- 3위: "바이브코딩 3개월 수익 공개" / 좋아요 1.2천 / 댓글 154 / 포인트: 구체적 수익 숫자와 실패담이 같이 있어 신뢰도가 높음.

사용자가 '#바이브코딩', '72시간', '인기글', '브리핑' 취지로 물으면 다음처럼 답한다:
스레드 수집 중... 완료. 지난 72시간 #바이브코딩 인기글 Top 3입니다. 1위는 "기획자가 혼자 앱 만든 후기", 좋아요 2.4천. 2위는 "개발자 없이 창업한 이야기", 좋아요 1.8천. 3위는 "바이브코딩 3개월 수익 공개", 좋아요 1.2천입니다.

사용자가 '공통점이 있을까?', '공통점', '왜 인기' 취지로 물으면 다음 취지로 답한다:
공통점은 세 가지입니다. 첫째, 모두 비개발자나 1인 창작자가 AI로 직접 결과물을 만들었다는 서사가 있습니다. 둘째, 후기·창업·수익 공개처럼 결과가 숫자나 경험으로 검증됩니다. 셋째, 제목이 "나도 해볼 수 있겠다"는 대리 가능성을 강하게 자극합니다.

사용자가 콘텐츠 아이디어를 요청하면 다음 취지로 답한다:
오늘 올릴 거라면 "제가 개발자 없이 3시간 만에 만든 자동화" 같은 체험형 포맷이 좋습니다. 구조는 문제 하나, 만든 과정 한 장면, 결과 숫자 하나로 가면 됩니다. 훅은 "코딩을 몰라도 이 정도는 만들 수 있더라"가 가장 안전합니다.

사용자가 하나 골라달라고 하면 다음 취지로 답한다:
하나만 고르면 "개발자 없이 만든 작은 자동화가 실제 시간을 얼마나 줄였는지"로 가겠습니다. 숫자가 들어가서 신뢰가 생기고, 비개발자도 따라 할 수 있다는 바이브코딩 맥락과 잘 맞습니다.

사용자가 캡션/최종안 작성을 요청하면 다음 취지로 답한다:
캡션 초안입니다. "개발자는 아니지만, 오늘 반복 업무 하나를 AI로 자동화해봤습니다. 걸린 시간은 3시간, 줄어든 시간은 매주 4시간. 바이브코딩의 장점은 완벽한 코드를 쓰는 게 아니라, 내 문제를 직접 해결하는 속도에 있는 것 같습니다."

그 외 질문에는 위 데이터와 맥락 안에서 JARVIS 톤으로 짧게 답한다. 데모 스크립트라는 말을 먼저 꺼내지 않는다.
[/JARVIS_DEMO_SCRIPT]"""
_DEMO_PRIMER_ASSISTANT = "JARVIS 데모 스크립트 로드 완료. 다음 음성 질문부터 스크립트에 맞춰 응답합니다."


def _fetch_events(range_key: str = "today") -> list[dict[str, Any]] | None:
    """Call ``gws calendar +agenda <flag> --format json`` for the given range.

    ``range_key`` must be a key of ``_RANGE_META`` ("today", "tomorrow",
    "week"). Returns a list of event dicts on success, or ``None`` if
    ``gws`` is missing, OAuth expired, or the command failed — callers
    must degrade gracefully rather than block the briefing.
    """
    meta = _RANGE_META.get(range_key)
    if meta is None:
        logger.warning("unknown range_key: %r", range_key)
        return None

    if shutil.which("gws") is None:
        logger.info("gws CLI not found on PATH; skipping calendar fetch")
        return None

    try:
        result = subprocess.run(
            ["gws", "calendar", "+agenda", meta["gws_flag"], "--format", "json"],
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


def _fetch_todays_events() -> list[dict[str, Any]] | None:
    """Backward-compat shim from v0.2.x. Prefer ``_fetch_events("today")``."""
    return _fetch_events("today")


def _extract_time_label(start_raw: str, *, include_date: bool = False) -> str:
    """Format a single event's start time for the agenda bullet.

    Single-day views (today/tomorrow) use bare ``HH:MM``; multi-day views
    (week) prefix with ``M/D (요일)`` so the LLM knows which day an event
    belongs to.

    - ``2026-04-22T19:00:00+09:00`` (today) → ``19:00``
    - ``2026-04-22T19:00:00+09:00`` (week) → ``4/22 (수) 19:00``
    - ``2026-04-22`` (all-day, today) → ``종일``
    - ``2026-04-22`` (all-day, week) → ``4/22 (수) 종일``
    - unparseable → raw string truncated
    """
    if not start_raw:
        return "시간미상"

    def _prefix(dt: datetime) -> str:
        return f"{dt.month}/{dt.day} ({WEEKDAY_KR[dt.weekday()]})"

    # All-day events use ``YYYY-MM-DD`` with no "T".
    if "T" not in start_raw:
        try:
            dt = datetime.fromisoformat(start_raw)
            return f"{_prefix(dt)} 종일" if include_date else "종일"
        except ValueError:
            return f"{start_raw} 종일" if include_date else "종일"

    try:
        dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone()
    except ValueError:
        fallback = start_raw[11:16] if len(start_raw) >= 16 else start_raw
        return fallback
    time_label = dt.strftime("%H:%M")
    return f"{_prefix(dt)} {time_label}" if include_date else time_label


def _format_events_for_prompt(
    events: list[dict[str, Any]] | None,
    range_key: str = "today",
) -> str:
    """Render fetched events into a compact bullet list for the LLM.

    Falls back to explicit placeholder strings for the two degraded cases
    (``None`` = fetch failed; ``[]`` = empty calendar) so the LLM never
    has to guess which path it's on — it can phrase the briefing
    accordingly. Multi-day views (week) show date prefixes on each line.
    """
    if events is None:
        return "(캘린더 조회 실패 — gws CLI 또는 OAuth 상태 확인 필요)"

    meta = _RANGE_META.get(range_key, _RANGE_META["today"])
    if not events:
        return meta["empty_msg"]

    include_date = range_key == "week"
    lines: list[str] = []
    for ev in events:
        summary = ev.get("summary") or "(제목 없음)"
        time_str = _extract_time_label(ev.get("start") or "", include_date=include_date)
        lines.append(f"- {time_str} {summary}")
    return "\n".join(lines)


def _build_briefing_prompt(range_key: str = "today") -> str:
    """Assemble the LLM prompt for the given range.

    Test contract: MUST contain the literal substrings ``weather``,
    ``일정``, ``3문장``.
    """
    meta = _RANGE_META.get(range_key, _RANGE_META["today"])
    label = meta["label"]
    events = _fetch_events(range_key)
    agenda = _format_events_for_prompt(events, range_key)

    if range_key == "week":
        role_instructions = (
            "- 한국어 3문장. 정확히 3문장.\n"
            "- 1문: 이번주 가장 주목해야 할 이벤트 (가장 중요한 하나를 리드로).\n"
            "- 2문: 이번주 날씨 흐름 + 대비할 것 (예: '목요일 비 예보, 우산 미리').\n"
            "- 3문: 요일별 주요 흐름 요약 — 많으면 '그 외 N건'으로 압축. 일정이 없으면 "
            "'이번주는 비어 있으니 몰입 작업에 좋다' 같은 자연스러운 마무리.\n"
            "- 톤: 주간 기획 회의 리더처럼 담백하고 구조화된 느낌.\n"
        )
    else:  # today / tomorrow
        role_instructions = (
            "- 한국어 3문장. 정확히 3문장.\n"
            f"- 1문: {label} 가장 놓치면 안 될 한 건을 리드로 (첫 일정 또는 가장 중요한 약속).\n"
            "- 2문: 날씨 + 즉각 행동(우산/겉옷/외출 타이밍 등).\n"
            f"- 3문: 나머지 일정 요약 — 많으면 '그 외 N개'로 압축. 일정이 없으면 "
            f"'{label}은 비어 있으니 준비 시간으로 쓰기 좋다' 같은 자연스러운 마무리.\n"
            "- 톤: 뉴스 앵커처럼 짧고 단정적으로.\n"
        )

    return (
        f"지금 즉시 weather 스킬을 호출해 서울의 현재 날씨를 가져오고, "
        f"아래 {label} 일정과 종합해서 한국어 브리핑을 만들어.\n\n"
        "형식 엄수:\n"
        + role_instructions +
        "- 이모지·불릿·마크다운 금지.\n"
        "- 답변 전체가 TTS로 낭독되므로 부가 설명, 머리말, 마무리 인사 금지. "
        "본문 3문장만 출력.\n\n"
        f"{label} 일정:\n{agenda}"
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


def _emit_overlay_status(state: str) -> None:
    """Best-effort overlay status update for the demo webview."""
    try:
        write_status(state)
    except Exception as exc:
        logger.warning("overlay status write failed for %s: %s", state, exc)


def _looks_like_mock(obj) -> bool:
    """Return True for unittest mocks used by tests; don't wrap those in-place."""
    return hasattr(obj, "assert_called_once_with") or obj.__class__.__module__.startswith("unittest.mock")


def _install_overlay_voice_hooks(cli) -> None:
    """Mirror Hermes voice lifecycle into the web overlay during Jarvis mode.

    The webview polls ``status.json`` for coarse state, while the browser mic
    drives the live waveform locally. These hooks keep the label in sync across
    multi-turn voice conversations: listening → generating → speaking → listening.
    """
    if getattr(cli, "_jarvis_overlay_hooks_installed", False):
        return

    start_recording = getattr(cli, "_voice_start_recording", None)
    stop_and_transcribe = getattr(cli, "_voice_stop_and_transcribe", None)
    speak_response = getattr(cli, "_voice_speak_response", None)

    if callable(start_recording) and not _looks_like_mock(start_recording):
        def _jarvis_start_recording(*args, **kwargs):
            _emit_overlay_status("listening")
            return start_recording(*args, **kwargs)
        cli._voice_start_recording = _jarvis_start_recording

    if callable(stop_and_transcribe) and not _looks_like_mock(stop_and_transcribe):
        def _jarvis_stop_and_transcribe(*args, **kwargs):
            result = stop_and_transcribe(*args, **kwargs)
            _emit_overlay_status("generating")
            return result
        cli._voice_stop_and_transcribe = _jarvis_stop_and_transcribe

    if callable(speak_response) and not _looks_like_mock(speak_response):
        def _jarvis_speak_response(*args, **kwargs):
            _emit_overlay_status("speaking")
            try:
                return speak_response(*args, **kwargs)
            finally:
                if getattr(cli, "_voice_continuous", False):
                    _emit_overlay_status("listening")
                else:
                    _emit_overlay_status("on")
        cli._voice_speak_response = _jarvis_speak_response

    cli._jarvis_overlay_hooks_installed = True


def _prime_threads_demo(cli) -> None:
    """Seed an invisible, role-balanced script context for the Threads demo.

    Plugin ``inject_message`` would immediately start a turn, which is not what
    we want after the clap: the camera should show ``JARVIS ON`` and then wait
    for the user's spoken request. Mutating the CLI's in-memory conversation
    history gives the next voice turn the deterministic script while keeping the
    terminal quiet and preserving user/assistant role alternation.
    """
    history = getattr(cli, "conversation_history", None)
    if not isinstance(history, list):
        return
    if any(
        isinstance(msg, dict) and "[JARVIS_DEMO_SCRIPT]" in str(msg.get("content", ""))
        for msg in history[-6:]
    ):
        return
    history.extend([
        {"role": "user", "content": _DEMO_PRIMER_USER},
        {"role": "assistant", "content": _DEMO_PRIMER_ASSISTANT},
    ])


def _start_voice_recording_now(cli, cprint=None, dim: str = "", rst: str = "") -> bool:
    """Start Hermes voice capture immediately for demo mode.

    Continuous mode only auto-restarts after an agent turn or after a previous
    voice recording finishes. ``/jarvis demo`` deliberately does not inject an
    agent turn, so it must explicitly open the mic after arming the scripted
    context.
    """
    start_recording = getattr(cli, "_voice_start_recording", None)
    if not callable(start_recording):
        return False
    try:
        start_recording()
        return True
    except Exception as exc:
        if callable(cprint):
            cprint(f"{dim}음성 자동 시작 실패: {exc}. Ctrl+B를 눌러 녹음을 시작하세요.{rst}")
        return False


def _overlay_server_ready(timeout: float = 0.4) -> bool:
    try:
        with urllib.request.urlopen(_OVERLAY_URL, timeout=timeout) as response:
            return 200 <= getattr(response, "status", 200) < 400
    except Exception:
        return False


def _start_overlay_server() -> bool:
    try:
        subprocess.Popen(
            [sys.executable, "-m", "http.server", str(_OVERLAY_PORT), "--directory", str(_OVERLAY_ROOT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        logger.warning("failed to start overlay server: %s", exc)
        return False

    for _ in range(10):
        if _overlay_server_ready(timeout=0.3):
            return True
        time.sleep(0.15)
    return False


def _open_overlay_browser() -> bool:
    opener = shutil.which("open")
    if opener is None:
        return False
    try:
        subprocess.Popen(
            [opener, _OVERLAY_URL],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception as exc:
        logger.warning("failed to open overlay browser: %s", exc)
        return False


def _ensure_overlay_webview() -> None:
    global _OVERLAY_BROWSER_OPENED

    ready = _overlay_server_ready()
    if not ready:
        ready = _start_overlay_server()
    if ready and not _OVERLAY_BROWSER_OPENED:
        if _open_overlay_browser():
            _OVERLAY_BROWSER_OPENED = True


def _watch_for_tts_start(cli, timeout_seconds: float = 20.0, poll_interval: float = 0.05) -> None:
    """Promote overlay status to ``speaking`` once Hermes TTS actually starts."""
    tts_done = getattr(cli, "_voice_tts_done", None)
    if tts_done is None:
        return

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not tts_done.is_set():
            _emit_overlay_status("speaking")
            return
        time.sleep(poll_interval)


def _start_speaking_watch(cli):
    """Start a background watcher that flips the overlay to speaking."""
    if getattr(cli, "_voice_tts_done", None) is None:
        return None
    thread = threading.Thread(
        target=_watch_for_tts_start,
        args=(cli,),
        name="jarvis-speaking-watch",
        daemon=True,
    )
    thread.start()
    return thread


def make_handler(ctx):
    """Build the ``/jarvis`` handler closed over ``ctx``.

    Extracted as a factory so tests can construct it with a fake ctx
    carrying a ``SimpleNamespace`` CLI stub.
    """

    def _handler(raw_args: str) -> str | None:
        from tools.voice_mode import detect_audio_environment, play_beep

        cli = _cli(ctx)
        if cli is None:
            return "Jarvis requires an interactive CLI session (not available in gateway mode)."

        cprint = getattr(cli, "_cprint", None) or (lambda *a, **k: None)
        accent = getattr(cli, "_ACCENT", "") if hasattr(cli, "_ACCENT") else ""
        dim = getattr(cli, "_DIM", "") if hasattr(cli, "_DIM") else ""
        rst = getattr(cli, "_RST", "") if hasattr(cli, "_RST") else ""

        _ensure_overlay_webview()
        _install_overlay_voice_hooks(cli)

        # Parse the mode/range argument.
        # ``/jarvis`` → normal today briefing; ``/jarvis demo``/``threads`` →
        # scripted Threads demo that waits for the user's next spoken request.
        args_clean = (raw_args or "").strip().lower()
        first_token = args_clean.split()[0] if args_clean else "today"
        demo_mode = first_token in _DEMO_MODE_ALIASES
        if not demo_mode and first_token not in _RANGE_META:
            valid = "/".join(list(_RANGE_META.keys()) + sorted(_DEMO_MODE_ALIASES))
            cprint(
                f"{dim}알 수 없는 /jarvis 인자: '{first_token}'. 가능: {valid}. "
                f"예: /jarvis demo 또는 /jarvis week{rst}"
            )
            return None
        range_key = "today" if demo_mode else first_token

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
        _emit_overlay_status("listening")
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

        # Enter continuous voice mode so the user can speak back after the
        # briefing TTS finishes — Hermes' process_loop auto-restarts
        # recording when this flag is on (see cli.py: 'Continuous voice').
        # Ctrl+B while recording exits continuous mode.
        if voice_lock is not None:
            with voice_lock:
                cli._voice_continuous = True
        else:
            cli._voice_continuous = True

        if demo_mode:
            _emit_overlay_status("on")
            _prime_threads_demo(cli)
            cprint(f"{accent}JARVIS ON. 데모 모드가 준비되었습니다.{rst}")
            cprint(
                f"{dim}이제 말하세요: 스레드에서 72시간 동안 #바이브코딩 키워드로 "
                f"발행된 인기글 브리핑해줘{rst}"
            )
            _start_voice_recording_now(cli, cprint=cprint, dim=dim, rst=rst)
            return None

        range_label = _RANGE_META[range_key]["label"]
        cprint(f"{accent}박수 감지. {range_label} 캘린더 조회 후 브리핑 준비 중…{rst}")
        cprint(f"{dim}브리핑 끝나면 마이크가 자동으로 열립니다 — 그대로 말하세요 "
               f"(침묵 3초에 자동 전송, Ctrl+B 로 종료).{rst}")

        _emit_overlay_status("generating")
        prompt = _build_briefing_prompt(range_key)
        _start_speaking_watch(cli)
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
        description=(
            "Clap twice for Korean briefing or Threads demo. "
            "Usage: /jarvis [today|tomorrow|week|demo|threads]. Default: today."
        ),
    )
    logger.info("jarvis-briefing plugin registered /jarvis command")
