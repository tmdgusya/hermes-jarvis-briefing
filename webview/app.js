const STATUS_ENDPOINT = '/jarvis-overlay/status.json';
const POLL_MS = 350;

const stateLabel = document.getElementById('stateLabel');
const statusText = document.getElementById('statusText');
const updatedAt = document.getElementById('updatedAt');

const COPY = {
  idle: {
    label: '대기 중',
    text: '/jarvis 실행을 기다리는 중',
  },
  on: {
    label: 'JARVIS ON',
    text: '음성 명령을 기다리고 있습니다',
  },
  listening: {
    label: '듣는 중',
    text: 'Jarvis가 박수와 음성 트리거를 대기 중입니다',
  },
  generating: {
    label: '생성 중',
    text: '브리핑을 정리하고 응답을 구성하는 중입니다',
  },
  speaking: {
    label: '읽어드리는 중',
    text: '완성된 브리핑을 음성으로 전달하는 중입니다',
  },
};

let lastState = 'idle';

function applyState(payload) {
  const state = payload?.state && COPY[payload.state] ? payload.state : 'idle';
  const copy = COPY[state];
  lastState = state;
  document.body.dataset.state = state;
  stateLabel.textContent = payload?.label || copy.label;
  statusText.textContent = copy.text;
  updatedAt.textContent = payload?.updated_at
    ? `updated ${new Date(payload.updated_at).toLocaleTimeString('ko-KR')}`
    : 'status: idle';
}

async function poll() {
  try {
    const res = await fetch(`${STATUS_ENDPOINT}?t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const payload = await res.json();
    applyState(payload);
  } catch (err) {
    if (lastState !== 'idle') {
      applyState({ state: 'idle' });
    }
  }
}

applyState({ state: 'idle' });
poll();
setInterval(poll, POLL_MS);
