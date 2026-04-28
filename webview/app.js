const STATUS_ENDPOINT = '/jarvis-overlay/status.json';
const POLL_MS = 350;

const stateLabel = document.getElementById('stateLabel');
const statusText = document.getElementById('statusText');
const updatedAt = document.getElementById('updatedAt');
const voiceWave = document.getElementById('voiceWave');
const waveCtx = voiceWave.getContext('2d');

const COPY = {
  idle: {
    label: '대기 중',
    text: '/jarvis 실행을 기다리는 중',
  },
  on: {
    label: 'JARVIS ON',
    text: '대화 모드가 켜졌습니다',
  },
  listening: {
    label: '듣는 중',
    text: '말하는 음성에 맞춰 파형이 반응합니다',
  },
  generating: {
    label: '생성 중',
    text: '답변을 정리하고 있습니다',
  },
  speaking: {
    label: '말하는 중',
    text: 'Jarvis 응답 음성에 맞춰 인터페이스가 움직입니다',
  },
};

let lastState = 'idle';
let analyser = null;
let frequencyData = null;
let micLevel = 0;
let micReady = false;
let micDenied = false;

function applyState(payload) {
  const state = payload?.state && COPY[payload.state] ? payload.state : 'idle';
  const copy = COPY[state];
  lastState = state;
  document.body.dataset.state = state;
  stateLabel.textContent = payload?.label || copy.label;
  statusText.textContent = micDenied && (state === 'listening' || state === 'speaking')
    ? `${copy.text} · 브라우저 마이크 권한을 허용하면 실제 파형이 보입니다`
    : copy.text;
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

async function initMicWave() {
  if (!navigator.mediaDevices?.getUserMedia) {
    micDenied = true;
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: true,
      },
      video: false,
    });
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    const audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(stream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.74;
    source.connect(analyser);
    frequencyData = new Uint8Array(analyser.frequencyBinCount);
    micReady = true;
  } catch (err) {
    micDenied = true;
  }
}

function resizeCanvas() {
  const rect = voiceWave.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.floor(rect.width * dpr));
  const height = Math.max(1, Math.floor(rect.height * dpr));
  if (voiceWave.width !== width || voiceWave.height !== height) {
    voiceWave.width = width;
    voiceWave.height = height;
    waveCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
}

function sampleMicLevel(now) {
  if (micReady && analyser && frequencyData) {
    analyser.getByteFrequencyData(frequencyData);
    let sum = 0;
    const usableBins = Math.min(96, frequencyData.length);
    for (let i = 2; i < usableBins; i += 1) {
      sum += frequencyData[i];
    }
    const average = sum / Math.max(1, usableBins - 2);
    const normalized = Math.min(1, average / 95);
    micLevel = micLevel * 0.62 + normalized * 0.38;
    return micLevel;
  }

  const active = lastState === 'listening' || lastState === 'speaking';
  const generated = active
    ? 0.28 + Math.sin(now / 140) * 0.12 + Math.sin(now / 71) * 0.08
    : 0.04 + Math.sin(now / 900) * 0.02;
  micLevel = micLevel * 0.82 + Math.max(0, generated) * 0.18;
  return micLevel;
}

function drawWave(now) {
  resizeCanvas();
  const rect = voiceWave.getBoundingClientRect();
  const width = rect.width;
  const height = rect.height;
  const centerX = width / 2;
  const centerY = height / 2;
  const active = lastState === 'listening' || lastState === 'speaking';
  const thinking = lastState === 'generating';
  const level = sampleMicLevel(now);
  const intensity = active ? level : thinking ? 0.16 : 0.06;

  waveCtx.clearRect(0, 0, width, height);
  waveCtx.save();
  waveCtx.translate(centerX, centerY);

  const rings = active ? 4 : 3;
  for (let ring = 0; ring < rings; ring += 1) {
    const baseRadius = Math.min(width, height) * (0.26 + ring * 0.073);
    const points = 180;
    const amp = (10 + ring * 7) * (0.35 + intensity * 1.9);
    const alpha = active ? 0.28 + intensity * 0.42 : 0.12;
    waveCtx.beginPath();
    for (let i = 0; i <= points; i += 1) {
      const theta = (Math.PI * 2 * i) / points;
      const wobble =
        Math.sin(theta * 5 + now / (240 - ring * 28)) * amp +
        Math.sin(theta * 9 - now / (330 + ring * 40)) * amp * 0.45;
      const radius = baseRadius + wobble;
      const x = Math.cos(theta) * radius;
      const y = Math.sin(theta) * radius;
      if (i === 0) waveCtx.moveTo(x, y);
      else waveCtx.lineTo(x, y);
    }
    waveCtx.closePath();
    waveCtx.strokeStyle = `rgba(86, 244, 255, ${alpha})`;
    waveCtx.lineWidth = 1.2 + intensity * 2.4;
    waveCtx.shadowColor = 'rgba(86, 244, 255, 0.7)';
    waveCtx.shadowBlur = active ? 12 + intensity * 30 : 8;
    waveCtx.stroke();
  }

  const glowRadius = Math.min(width, height) * (0.21 + intensity * 0.045);
  const gradient = waveCtx.createRadialGradient(0, 0, 0, 0, 0, glowRadius);
  gradient.addColorStop(0, `rgba(86, 244, 255, ${0.18 + intensity * 0.28})`);
  gradient.addColorStop(0.62, `rgba(86, 244, 255, ${0.04 + intensity * 0.12})`);
  gradient.addColorStop(1, 'rgba(86, 244, 255, 0)');
  waveCtx.fillStyle = gradient;
  waveCtx.beginPath();
  waveCtx.arc(0, 0, glowRadius, 0, Math.PI * 2);
  waveCtx.fill();

  waveCtx.restore();
  requestAnimationFrame(drawWave);
}

applyState({ state: 'idle' });
poll();
setInterval(poll, POLL_MS);
initMicWave();
requestAnimationFrame(drawWave);
window.addEventListener('resize', resizeCanvas);
