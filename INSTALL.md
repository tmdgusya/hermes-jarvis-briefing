# INSTALL.md — Agent Runbook for Jarvis Briefing

> **이 문서는 사람용이 아닙니다.** 이 문서는 **마케터(비기술 사용자)의 로컬 머신에서 동작하는 AI 에이전트** (Claude Code, Hermes 자기 자신, 또는 동등한 툴) 가 읽고 설치 전체 과정을 자동으로 수행하도록 쓰인 runbook 입니다. 사용자는 API 키 붙여넣기와 macOS 마이크 권한 승인 두 번만 하면 됩니다.
>
> **에이전트에게 지시 사항**: 각 Phase를 순서대로 처리합니다. 각 Phase 의 종료 조건이 충족되어야 다음 Phase로 넘어갑니다. 실패 시 해당 Phase 의 Troubleshoot 섹션을 먼저 확인하고, 거기서도 해결 안 되면 사용자에게 구체적 오류 + 원인 가설 + 가능한 action 2~3개를 제시합니다. 혼자 추측해서 destructive action(rm -rf, reset, force 등) 하지 않습니다.

---

## Phase 0 — Precheck (5분)

### 목표
설치 진행 가능 여부 판단. **여기서 실패하면 설치를 시작하지 않습니다.**

### 체크리스트

1. **OS 확인**
   ```bash
   uname -s
   sw_vers 2>/dev/null | head -3
   ```
   - 허용: macOS (Darwin), Linux
   - 차단: Windows 순수 환경 (WSL은 OK 하지만 마이크 브리지 별도 설정 필요 — INSTALL 중단하고 사용자에게 "이 플러그인은 현재 macOS 위주로 테스트됐습니다. Windows WSL 에서 시도하려면 PulseAudio 브리지가 필요합니다" 안내).

2. **네트워크**
   ```bash
   curl -fsSL -m 5 https://github.com >/dev/null && echo OK || echo FAIL
   curl -fsSL -m 5 https://wttr.in/Seoul?format=3 >/dev/null && echo OK || echo FAIL
   ```
   둘 다 OK 이어야 함.

3. **쉘 = bash 또는 zsh**
   ```bash
   echo "$SHELL"
   ```
   그 외 쉘 (fish 등) 은 사용 자체는 가능하지만 PATH 설정 안내가 달라지므로 사용자에게 쉘 이름을 알려주고 확인받기.

4. **디스크**
   ```bash
   df -h ~ | tail -1
   ```
   최소 2GB 여유. 부족하면 중단.

5. **Python 3.11+ 존재 확인**
   ```bash
   command -v python3.11 || command -v python3.12 || command -v python3.13 || echo MISSING
   python3 --version 2>&1
   ```
   - Python 3.11+ 가 이미 있으면 OK.
   - 없으면 Phase 1 의 Homebrew 경로로 설치 계획.

6. **Homebrew 존재 확인 (macOS)**
   ```bash
   command -v brew || echo MISSING
   ```
   - macOS 에서 brew 가 없으면 Phase 1 에서 설치.
   - Linux 에서는 대신 apt/dnf 기반으로 portaudio 를 설치.

### Phase 0 종료 조건
- 모든 체크 OK → Phase 1 진행
- 차단 요인 발견 → 사용자에게 어떤 체크가 막았는지 + 해결 action 제시 후 대기

---

## Phase 1 — Hermes 설치 (10~15분)

### 목표
`hermes` 커맨드가 터미널에서 실행 가능한 상태.

### 이미 설치돼 있는가?

```bash
command -v hermes && hermes --version 2>&1 || echo NOT_INSTALLED
```

- Hermes가 이미 있고 `v0.10.0` 이상이면 **Phase 2 로 건너뜁니다**.
- 구버전이면 업데이트 제안 (`hermes update` 또는 스크립트 재실행).
- 없으면 아래 설치 진행.

### 설치 명령

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash -s -- --skip-setup
```

`--skip-setup` 을 붙인 이유: 설치 스크립트의 대화형 wizard가 에이전트-실행 환경에서 hang 할 수 있음. Setup 은 Phase 2 에서 따로 처리.

### 확인

```bash
source ~/.zshrc 2>/dev/null || source ~/.bashrc 2>/dev/null
command -v hermes && hermes --version
```

버전 출력이 나오면 설치 성공.

### Troubleshoot

| 증상 | 가설 | 대응 |
|---|---|---|
| `brew: command not found` (macOS) | Homebrew 미설치 | 사용자에게 허락 받고 `NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` 실행 |
| `portaudio` 빌드 실패 | Xcode Command Line Tools 없음 | `xcode-select --install` 을 사용자가 수동 승인해야 함 — GUI 다이얼로그 뜸. 승인 후 재시도. |
| `pip install` 중 `Failed building wheel for faster-whisper` | ctranslate2 의존 실패 | `--no-faster-whisper` 플래그는 없음. 대신 `pip install hermes-agent[voice]` 대신 `pip install hermes-agent` 로 낮춰 설치하고 TTS/STT 는 cloud 모드로 — 단 로컬 음성 녹음은 여전히 sounddevice 만 있으면 됨. 사용자에게 옵션 설명. |
| `hermes: command not found` after install | PATH 문제 | `export PATH="$HOME/.hermes/bin:$PATH"` 를 쉘 rc 에 추가. |

### Phase 1 종료 조건
- `hermes --version` 이 v0.10.0 이상을 출력

---

## Phase 2 — Hermes 모델/인증 설정 (5분, 사용자 상호작용 필수)

### 목표
Hermes 가 Anthropic Claude API 를 사용할 수 있는 상태.

### 키 유무 확인

```bash
hermes auth status 2>&1 | head -20
```

이미 `ANTHROPIC_API_KEY` 가 설정되어 있고 active provider 가 anthropic 이면 **Phase 3 으로 건너뜁니다**.

### 키 발급 안내

사용자에게 정확히 다음 문구를 보여줍니다:

> **Anthropic API 키가 필요합니다.**
>
> 1. 브라우저에서 https://console.anthropic.com/ 접속
> 2. 로그인 (구글/이메일) 후 왼쪽 메뉴에서 API Keys
> 3. "Create Key" 클릭 → 이름 `jarvis-briefing` 같은 식으로 지정 → Create
> 4. 나타난 키 (`sk-ant-api03-...`) 를 **이 창에 붙여넣어 주세요**.
>
> ⚠ 이 키는 사용량에 따라 과금됩니다. /jarvis 한 번 실행 시 $0.01 미만. 걱정되면 Anthropic 대시보드의 Usage Limits 에서 월 한도 설정 가능.

### 키 등록

사용자가 키를 붙여넣으면:

```bash
# 에이전트는 키를 직접 shell 변수로 다루지 말고 안전하게 저장
hermes login anthropic
# 또는 직접 env 저장:
echo "ANTHROPIC_API_KEY=<user-provided-key>" >> ~/.hermes/.env
```

이후 검증:

```bash
hermes auth status
hermes chat --help 2>&1 | tail -5
```

`auth status` 에서 anthropic 이 active 로 나오면 OK.

### 모델 선택

```bash
hermes model set anthropic claude-opus-4-7-20251119 2>&1
# 또는 기본값으로 두어도 OK (Hermes 가 active provider 의 기본 모델을 씀)
```

### Phase 2 종료 조건
- `hermes auth status` 출력에서 anthropic = active
- `hermes chat` 명령이 에러 없이 뜰 수 있음 (세션 시작해서 ctrl+c 로 종료하는 걸로 검증 가능)

### Troubleshoot

| 증상 | 대응 |
|---|---|
| `Invalid API key` | 사용자에게 키를 다시 복사하도록 요청. Anthropic 콘솔에서 키 앞뒤 공백 / `sk-ant-` prefix 포함 여부 확인. |
| `Rate limit exceeded` | Anthropic 계정이 free tier 이고 사용량 소진. 사용자에게 billing 페이지에서 크레딧 추가 안내. |
| `Model not found` | 지정한 model name 이 현재 Anthropic 에서 제공하지 않음. `hermes model list anthropic` 로 유효 모델 확인 후 재설정. |

---

## Phase 2.5 — Google Calendar 접근 (gws CLI) 설정 (5~10분, 사용자 상호작용)

### 목표
`gws` CLI 가 설치되어 있고 Google 계정 OAuth 가 완료되어 `gws calendar +agenda --today --format json` 이 성공적으로 JSON 을 반환하는 상태. 이 단계가 없어도 플러그인은 동작하지만 브리핑에서 일정 부분이 "조회 실패" 문구로 대체됩니다 — 기능이 반쪽이 됩니다.

### 이미 설치/인증 돼 있는가?

```bash
command -v gws && gws calendar +agenda --today --format json 2>&1 | head -3
```

- `count`, `events`, `timeMin`, `timeMax` 필드가 있는 JSON 이 나오면 **Phase 3 으로 건너뜁니다**.
- `gws: command not found` → 아래 설치.
- `auth ... error` / `token expired` 등 → OAuth 플로우 실행.

### 설치

macOS (Homebrew):
```bash
brew install gws
# 또는 커뮤니티 탭이면:
# brew tap kr/gws && brew install gws
```

Linux (prebuilt 있으면):
```bash
# 프로젝트 공식 설치 안내에 따름 (배포 전제 이미 확인된 경로가 있다면 여기 채우기)
```

*참고: 회사/프로젝트 내부 배포판이면 그에 맞는 설치 경로를 사용자와 확인. 에이전트가 마음대로 패키지 채우지 말 것.*

### OAuth 인증

```bash
gws auth login 2>&1 | head -20
# 또는 구체적 하위 커맨드가 다르면 gws --help 로 확인
```

에이전트는 사용자에게 다음을 안내:

> **Google 계정 인증을 진행합니다.**
>
> 1. 터미널에 표시되는 URL을 복사해서 브라우저에 붙여넣기
> 2. Google 계정으로 로그인 (브리핑에 쓸 캘린더가 있는 계정)
> 3. 권한 승인 화면에서 **Calendar read access** 체크 — 필수
> 4. 완료 후 브라우저가 반환하는 코드/토큰을 터미널에 붙여넣기 (CLI가 자동 열어주는 창이면 그대로 두기)
>
> ⚠ "Jarvis Briefing" 프로젝트로 앱 이름이 뜰 수도, gws 자체 OAuth client 로 뜰 수도 있습니다 — gws 배포 방식에 따름.

### 검증

```bash
gws calendar +agenda --today --format json
# → {"count": N, "events": [...], ...} 형태 JSON 나와야 함

gws calendar +agenda --tomorrow --format json
# → 내일 일정도 조회 가능하면 OK
```

에이전트는 출력의 `count` 가 0 이상 정수이고 `events` 가 배열이면 성공으로 판단.

### Troubleshoot

| 증상 | 원인 | 대응 |
|---|---|---|
| `gws: command not found` | 설치 안 됨 / PATH 누락 | `brew install` 재시도. 이미 설치돼 있으면 `brew link gws` 또는 PATH 추가. |
| `error: ... unauthorized` | OAuth 토큰 없음/만료 | `gws auth login` 재실행 → 웹 플로우 재승인 |
| `error: insufficient scope` | Calendar scope 권한 없이 인증됨 | `gws auth logout` 후 `login` 다시, 권한 화면에서 Calendar 체크 반드시 승인 |
| `Using keyring backend: ...` stderr 메시지 | **정상** — 무해한 stderr 안내. stdout 의 JSON 만 파싱하면 됨 | 무시 |
| 내 Google 계정이 여러 개라 엉뚱한 계정으로 인증됨 | 원하는 계정으로 OAuth 를 못 맞춤 | `gws auth logout --all` 후 원하는 계정만 브라우저에 로그인한 상태에서 `login` 재실행 |

### Phase 2.5 종료 조건
- `gws calendar +agenda --today --format json` 명령이 exit code 0 + `events` 배열을 포함하는 JSON 출력
- 또는 사용자가 "일정 통합 없이 날씨만으로도 괜찮다" 명시적으로 확인 (스킵)

---

## Phase 3 — 플러그인 설치 (2분)

### 목표
`/jarvis` 가 Hermes 세션에서 호출 가능한 상태.

### 설치 명령

```bash
hermes plugins install tmdgusya/hermes-jarvis-briefing
```

*참고: `tmdgusya/hermes-jarvis-briefing` 는 이 플러그인의 GitHub 위치. 레포 변경 시 이 문서를 업데이트.*

Hermes 가 이 레포를 `~/.hermes/plugins/jarvis-briefing/` 에 clone 하고 다음 세션부터 자동 등록.

### 확인

```bash
hermes plugins list 2>&1 | grep jarvis
# → "jarvis-briefing (0.1.0)" 비슷한 줄이 보여야 함
```

세션을 하나 열어서 `/help` 에 jarvis 가 뜨는지:

```bash
hermes chat <<< "/help" 2>&1 | grep -A1 jarvis || echo "NOT_REGISTERED"
```

`/jarvis` 가 help 출력에 포함되어야 합니다.

### Troubleshoot

| 증상 | 원인 | 대응 |
|---|---|---|
| `plugin jarvis-briefing installed but / jarvis not registered` | 기존 Hermes core 버전에 jarvis 가 이미 있음 (충돌) | `hermes plugins list` 로 플러그인 상태 확인. core에 있으면 우리 플러그인이 silent skip 됨 → Hermes 를 최신 main 으로 업데이트 (`hermes update`) 하거나 core 에 jarvis 가 없는 버전으로 이동. |
| `git clone failed` | GitHub SSH 키 문제 또는 private repo | HTTPS URL 로 수동 clone: `git clone https://github.com/tmdgusya/hermes-jarvis-briefing.git ~/.hermes/plugins/jarvis-briefing` |
| `ImportError: No module named 'numpy'` | `[voice]` extras 가 제대로 설치 안 됨 | Phase 1 로 돌아가서 `pip install 'hermes-agent[voice]'` 재실행 |

### Phase 3 종료 조건
- `hermes plugins list` 에 `jarvis-briefing` 표시
- 세션 안에서 `/help` 가 jarvis 항목을 포함

---

## Phase 4 — 마이크 권한 및 첫 실행 (3분, 사용자 상호작용 필수)

### 목표
`/jarvis` 를 실제로 돌려 **박수 → 브리핑 → TTS** 한 사이클이 끝나는 것.

### 사용자에게 안내

다음 문구를 그대로 보여줍니다:

> **곧 Hermes 세션을 시작합니다.**
>
> 1. 세션이 열리면 `/jarvis` 를 입력하고 엔터
> 2. macOS 가 **"Terminal이 마이크를 사용하려 합니다"** 다이얼로그를 한 번 띄울 수 있습니다. **"확인"** 클릭하세요. 한 번만 승인하면 이후로 묻지 않습니다.
> 3. 짧은 삐- 소리가 들리면 박수를 **두 번** 치세요 (손뼉 두 번, 0.3~3초 간격).
> 4. 확인 삐삐 소리가 들리고 한국어 브리핑이 음성으로 재생됩니다.

### 실행

```bash
hermes chat
```

세션이 열리면 `/jarvis` 를 `_pending_input` 에 주입하거나 사용자에게 직접 치도록 안내.

### 예상 출력

```
Jarvis listening. 박수 두 번으로 브리핑 시작 (30초 타임아웃).
[beep 880Hz]
[사용자 박수 x2]
[beep 1320Hz x2]
박수 감지. 브리핑 준비 중…
[TTS] "오늘 가장 놓치면 안 될 일은 ..."
```

### 성공 기준 체크리스트

- [ ] "Jarvis listening" 메시지 1초 이내 표시
- [ ] 준비 비프 청취
- [ ] 박수 두 번이 감지됨
- [ ] 확인 비프 청취
- [ ] 한국어 3문장 브리핑 TTS 재생
- [ ] 브리핑 종료 후 Hermes 프롬프트로 복귀

### Troubleshoot — 박수가 감지되지 않음

이 경우 `/jarvis` 가 자동으로 **관측된 최대 RMS** 를 출력합니다. 에이전트는 그 숫자를 읽고 아래 중 하나를 수행:

| 관측 RMS | 해석 | 자동 action |
|---|---|---|
| < 50 | 마이크 입력 자체가 없음 — 권한 문제 | 사용자에게 "시스템 설정 → 개인정보 보호 → 마이크 → Terminal 체크박스 활성화" 안내. 승인 후 세션 재시작 필요. |
| 50~1200 | 마이크는 잡히지만 박수가 약함 | `~/.hermes/plugins/jarvis-briefing/clap_detector.py` 의 `CLAP_RMS_THRESHOLD` 를 peak_rms × 0.7 값 (소수점 버리고 정수) 으로 교체. 에이전트가 sed 로 자동 수정 가능: `sed -i '' 's/CLAP_RMS_THRESHOLD = 1200/CLAP_RMS_THRESHOLD = <new>/' ~/.hermes/plugins/jarvis-briefing/clap_detector.py` |
| 1200~3000 | 임계값 통과했지만 더블 패턴 실패 | 박수 간격 안내 (0.3~3초 사이). 너무 빠르거나 너무 느린 경우. |
| > 3000 | 감지 로직에 버그? | 사용자에게 로그 덤프 요청하고 GitHub Issues 에 올리도록 안내. |

### Troubleshoot — TTS 가 안 들림

먼저 TTS 엔진이 활성화되어 있는지 확인합니다.

```bash
hermes tools list 2>&1 | grep -i tts
```

없으면:

```bash
hermes setup tts  # Edge TTS 기본 설치 (무료, API 키 불필요)
```

Jarvis 데모는 한국어 문장을 읽기 때문에 Edge TTS 음성도 한국어 화자로 맞춰야 합니다. 영어 음성(`en-US-*`)으로 한국어를 합성하면 `No audio was received`가 나면서 파일이 0바이트로 생성될 수 있습니다.

```bash
hermes config set tts.edge.voice ko-KR-SunHiNeural
```

간단한 재생 테스트:

```bash
python - <<'PY'
import json, os, sys
sys.path.insert(0, os.path.expanduser('~/.hermes/hermes-agent'))
from tools.tts_tool import text_to_speech_tool
from tools.voice_mode import play_audio_file
mp3='/tmp/jarvis_tts_test.mp3'
res=json.loads(text_to_speech_tool('테스트입니다. 자비스 음성이 들려야 합니다.', output_path=mp3))
print(res)
if res.get('success') and os.path.exists(mp3):
    print('play_result:', play_audio_file(mp3))
PY
```

### Troubleshoot — 브리핑이 한국어가 아님

Edge TTS 설정이 한국어 화자가 아닐 수 있습니다:

```bash
hermes config set tts.edge.voice ko-KR-SunHiNeural
```

### Phase 4 종료 조건
- 성공 기준 체크리스트 6개 모두 통과

---

## Phase 5 — 일상 사용 가이드 (완료 후)

설치가 끝나면 사용자에게 다음 요약을 출력:

```
✅ Jarvis Briefing 설치 완료

매일 아침 쓰려면:
1. 터미널에서 `hermes chat`
2. `/jarvis` 입력
3. 박수 두 번
4. 브리핑 청취

일정은 자동으로 Google Calendar primary 에서 당일 이벤트를 읽습니다.
  - 브리핑에 빠진 일정이 있다면 해당 이벤트가 다른 캘린더에 있을 가능성 →
    gws 기본 캘린더 설정 확인
  - OAuth 만료 시 `gws auth login` 재실행

문제가 생기면:
  hermes doctor     # 전체 진단
  hermes plugins list                      # 플러그인 상태
  cat ~/.hermes/plugins/jarvis-briefing/README.md   # 전체 문서
```

---

## Phase 6 (선택) — 쉘 alias 심기

사용자가 매번 `hermes chat` → `/jarvis` 를 치는 대신 **터미널에서 `jarvis` 한 단어**로 즉시 실행하고 싶다면:

### 확인

```bash
grep -q "alias jarvis=" ~/.zshrc ~/.bashrc 2>/dev/null && echo ALREADY_ALIASED
```

### 설정 (zsh)

```bash
cat >> ~/.zshrc <<'EOF'

# Jarvis Briefing — 세션 열고 /jarvis 자동 실행
alias jarvis='hermes chat --initial-message "/jarvis"'
EOF
source ~/.zshrc
```

*참고: `--initial-message` 플래그가 있는지 버전 따라 다름. 없으면 expect 스크립트 같은 대체 필요. `hermes chat --help` 로 확인.*

### 검증

```bash
command -v jarvis && type jarvis
```

이후 사용자는 어디서든 `jarvis` 한 단어로 실행 가능.

---

## Phase 7 (선택) — cron 으로 자동 브리핑

매일 아침 7시에 cron 이 세션을 띄워서 자동 브리핑:

```bash
hermes cron add --name jarvis-morning --schedule "0 7 * * *" --command "/jarvis"
hermes cron list
```

**제약**: cron 트리거는 `hermes chat` 같은 상호작용 세션이 아닌 백그라운드 세션이라 마이크 접근이 제한됩니다. **박수 트리거 자체는 백그라운드에서 동작하지 않습니다**. cron 은 오히려 "`/jarvis` 대신 직접 brief 프롬프트를 주입" 하는 용도로 쓰는 게 더 적합. 사용자에게 trade-off 설명.

---

## 최종 Sanity Check

에이전트는 설치 완료 보고 전에 다음을 한 번 더 실행:

```bash
# 1. 플러그인 로드 확인
hermes plugins list | grep -i jarvis

# 2. 테스트 (하드웨어 불필요)
cd ~/.hermes/plugins/jarvis-briefing && pytest tests/ -q 2>&1 | tail -5

# 3. Hermes doctor
hermes doctor 2>&1 | tail -20
```

테스트가 16/16 통과하고 doctor 가 critical error 없음을 확인한 뒤 사용자에게 "설치 완료" 보고.

---

## 에이전트 작업 원칙

1. **확인 없이 destructive action 금지** — `rm -rf`, `git reset --hard`, `brew uninstall` 등은 반드시 사용자에게 이유 + 대안 제시 후 승인 받기.
2. **각 Phase 종료 조건이 만족됐는지 스스로 검증** — 다음 Phase 로 넘기기 전에 grep/test/echo 로 확인.
3. **에러 시 가설 3개 제시** — "x, y, z 가 원인일 수 있습니다. 확인하려면 A/B/C 중 어떤 방식이 편하신가요?" 스타일.
4. **사용자의 인내 비용 인식** — 각 Phase 끝에 진행률 표시 ("Phase 3/7 완료, 예상 남은 시간 10분").
5. **로그 수집** — 문제 시 `~/.hermes/logs/install-<timestamp>.log` 에 단계별 stdout/stderr 저장. 마지막에 이 경로를 사용자에게 알려주기.
