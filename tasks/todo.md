# Jarvis Threads Demo 작업 계획

## 목표
- 박수 2번으로 웹 오버레이가 즉시 `JARVIS ON` 상태가 되도록 한다.
- 이후 사용자가 음성으로 “스레드에서 72시간 동안 #바이브코딩 키워드로 발행된 인기글 브리핑해줘”라고 말하면 빠르고 안정적으로 데모용 인기글 Top 3 브리핑을 하도록 한다.
- 후속 질문 “공통점이 있을까?”에 대해 자연스럽게 공통점을 답하도록 한다.
- 기존 `/jarvis [today|tomorrow|week]` 캘린더 브리핑 기능은 깨지지 않게 유지한다.

## 접근
1. 기존 `/jarvis` 기본 동작은 유지한다.
2. `/jarvis demo` 또는 `/jarvis threads` 같은 명시적 데모 모드를 추가한다.
3. 데모 모드에서는 박수 감지 후 브리핑 프롬프트를 즉시 주입하지 않고, `JARVIS ON` 오버레이 + continuous voice mode를 켠 뒤 사용자의 다음 음성 입력을 기다린다.
4. 모델이 빠른 모델이어도 흔들리지 않도록 conversation history에 비공개 데모 스크립트 프라이머(user/assistant pair)를 넣어 다음 두 발화를 사실상 스크립트대로 응답하게 한다.
5. 오버레이 bridge/webview에 `on` 상태를 추가한다.
6. 테스트를 추가/수정하고 전체 pytest를 실행한다.

## 변경 예상 파일
- `__init__.py`: 데모 모드 인자 파싱, 프라이머 주입, JARVIS ON 상태 emit
- `overlay_bridge.py`: `on` 상태 label 추가
- `webview/app.js`, `webview/styles.css`: `on` 상태 문구/시각 효과 추가
- `tests/test_handler.py`, `tests/test_overlay_bridge.py`: 회귀 테스트 추가
- `README.md`, `plugin.yaml`: 사용법 업데이트

## 검증
- `pytest tests/ -v`
- 데모 실행 흐름: `hermes chat` → `/jarvis demo` → 박수 2번 → 화면 `JARVIS ON` 확인 → 음성 질문 2개 확인

## Review
- 구현 완료: `/jarvis demo`/`/jarvis threads` 데모 모드를 추가했다.
- 박수 2번 성공 후 오버레이 상태를 `on`으로 쓰며 웹뷰에는 `JARVIS ON`이 표시된다.
- 데모 모드는 즉시 LLM 턴을 시작하지 않고 continuous voice + TTS를 켠 다음, 비공개 user/assistant 프라이머를 세션 히스토리에 넣어 다음 음성 질문부터 스크립트 기반으로 빠르게 응답한다.
- 기존 `/jarvis`, `/jarvis today`, `/jarvis tomorrow`, `/jarvis week` 흐름은 그대로 유지했다.
- standalone 테스트 환경에서 Hermes 본체가 PYTHONPATH에 없어도 handler 테스트가 돌도록 `tools.voice_mode` 테스트 stub을 추가했다.
- 검증: `pytest tests/ -v` → 39 passed.
