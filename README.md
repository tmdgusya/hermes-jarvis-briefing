# Jarvis Briefing — Hermes Agent 플러그인

`/jarvis` 슬래시 커맨드 하나로 박수 두 번을 쳐서 **한국어 아침 브리핑**을 음성으로 받습니다. 날씨는 Hermes 기본 `weather` 스킬 (wttr.in) 이 담당하고, 할 일 목록은 환경변수 또는 기본 더미 3개를 씁니다.

## 무엇을 하는가

- `/jarvis` 입력 → Hermes 가 voice mode + TTS 자동 활성화
- 준비 비프 → 박수 두 번 (3초 이내) → 확인 비프 → 브리핑 시작
- LLM 이 `weather` 스킬을 호출하고 오늘의 할 일을 결합해 **3문장 한국어 브리핑**을 생성
- TTS 로 자동 낭독

감지 실패 시 타임아웃 메시지에 **관측된 최대 RMS 수치 + 조정 가이드**가 자동 표시됩니다. 튜닝이 쉬워집니다.

## 요구 사항

- Hermes Agent 가 설치되어 있고 `hermes chat` 세션을 열 수 있는 상태
- `[voice]` 엑스트라 설치됨 (`pip install hermes-agent[voice]`) — `sounddevice`, `numpy`, `faster-whisper`
- macOS / Linux 에서 마이크 권한이 터미널에 부여됨
- `weather` 스킬이 `~/.hermes/skills/productivity/weather/SKILL.md` 에 존재 (Hermes 표준 번들로 제공됨)

자세한 사전 체크는 `INSTALL.md` (agent runbook) 참고.

## 설치

### 방법 1: `hermes plugins install`

```bash
hermes plugins install tmdgusya/hermes-jarvis-briefing
```

Hermes 가 이 레포를 `~/.hermes/plugins/jarvis-briefing/` 에 clone 하고 자동 등록합니다. 이후 세션에서 `/jarvis` 로 바로 사용.

### 방법 2: 수동 clone (개발용)

```bash
git clone https://github.com/tmdgusya/hermes-jarvis-briefing ~/.hermes/plugins/jarvis-briefing
```

Hermes 가 시작 시 user-plugins 디렉토리를 자동 스캔하므로 추가 등록이 필요 없습니다.

## 사용

```bash
hermes chat
# 세션 안에서:
/jarvis
```

- 세션 콘솔에 "Jarvis listening. 박수 두 번으로 브리핑 시작 (30초 타임아웃)." 표시
- 짧은 비프 한 번이 들리면 준비 완료 — **박수 두 번 (0.3초~3초 간격)**
- 확인 비프 두 번 + 브리핑 TTS

### 할 일 목록 커스텀

기본값 대신 내 실제 할 일을 쓰려면 환경변수로:

```bash
export JARVIS_TODOS="오전 9시 병원 예약
오후 2시 투자 리뷰 미팅
저녁 7시 헬스"
hermes chat
/jarvis
```

`JARVIS_TODOS` 는 줄바꿈으로 항목을 구분합니다. 비어 있으면 플러그인 기본 3개가 사용됩니다.

## 튜닝

박수 감지 임계값이 환경에 맞지 않으면 `/jarvis` 타임아웃 메시지가 **관측된 최대 RMS** 와 **제안값**을 알려줍니다. 그 제안값으로 플러그인의 `clap_detector.py` 상단 상수를 교체하면 됩니다.

| 변수 | 기본값 | 의미 |
|---|---|---|
| `CLAP_RMS_THRESHOLD` | 1200 | 박수로 인정할 최소 RMS (int16) |
| `CLAP_WINDOW_SECONDS` | 3.0 | 두 박수 사이 허용 간격 (초) |
| `CLAP_COOLDOWN_SECONDS` | 0.3 | 같은 박수가 두 청크에 걸쳐 두 번 카운트되는 걸 방지 |

### 임계값이 너무 높음 (박수 안 잡힘)
- 현재 1200 기본값은 MacBook 내장 마이크에서 실측된 값. 스튜디오 마이크나 외장 USB 마이크면 게인이 높아서 바로 잡힙니다.
- 잡히지 않으면 `/jarvis` 가 피드백한 peak_rms × 0.7 근처로 낮춰보세요.

### 임계값이 너무 낮음 (오탐 많음)
- 책상 두드림, 문 닫힘, 키보드 타격이 잡히면 임계값을 1500~2000 으로 올리거나 `CLAP_WINDOW_SECONDS` 를 1.5~2.0 으로 좁혀 우연한 더블 트리거를 줄이세요.

## 테스트

```bash
cd ~/.hermes/plugins/jarvis-briefing
pytest tests/ -v
```

16 개 테스트 (감지 로직 10 + 핸들러 오케스트레이션 4 + 프롬프트 커스텀 2). 하드웨어 없이 완결적으로 돌아갑니다.

## 제약 사항

- **macOS 에서 동일 세션에 `/voice` 를 먼저 쓰고 `/jarvis` 를 쓰면 스트림 재개 이슈가 있을 수 있음** — sounddevice/CoreAudio 의 알려진 버그. `/jarvis` 를 먼저 쓰거나 세션을 재시작하세요.
- 마이크가 SSH/Docker/WSL 환경이면 작동하지 않습니다 (`detect_audio_environment` 가 바로 bail).
- 한 세션 안에서 `/jarvis` 를 여러 번 연속 호출하면 sounddevice 재개 이슈로 hang 가능. 현재는 세션 수명당 한 번 사용을 권장.

## 라이선스

MIT. `LICENSE` 파일 참조.

## 관련 자료

- 구현 과정 전체 walkthrough: [fastcampus-hermes-agent-curriculum/walkthroughs/jarvis-clap-briefing](https://github.com/tmdgusya/fastcampus-hermes-agent-curriculum/tree/main/walkthroughs/jarvis-clap-briefing)
- 설치 자동화 runbook (AI agent 전용): `INSTALL.md`
