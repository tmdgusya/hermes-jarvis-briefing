# Jarvis Overlay Webview

간단 실행 예시:

```bash
cd ~/.hermes
python3 -m http.server 8765
```

브라우저에서 다음 주소를 엽니다.

- 웹뷰: http://localhost:8765/plugins/jarvis-briefing/webview/
- 상태 JSON: http://localhost:8765/jarvis-overlay/status.json

동작 방식:
- `/jarvis` 플러그인이 `~/.hermes/jarvis-overlay/status.json` 을 갱신
- 웹뷰가 `/jarvis-overlay/status.json` 을 polling 해서 상태를 시각화

프로토타입 상태는 3개만 사용합니다.
- listening → 듣는 중
- generating → 생성 중
- speaking → 읽어드리는 중
