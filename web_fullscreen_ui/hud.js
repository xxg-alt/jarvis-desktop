/* J.A.R.V.I.S. 全屏战术魔盒
   UI state bridge + dual waveform reactor + 3D parallax + telemetry */
"use strict";

const $ = (id) => document.getElementById(id);
const TAU = Math.PI * 2;
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const STATE_META = {
  IDLE:       { label: "系统待命", code: "IDLE // STANDBY", color: [0, 240, 255] },
  LISTENING:  { label: "正在聆听", code: "LISTENING // AUDIO INPUT", color: [0, 240, 255] },
  THINKING:   { label: "核心推理中", code: "THINKING // NEURAL CORE", color: [255, 184, 0] },
  ACTING:     { label: "正在执行", code: "ACTING // TASK PIPELINE", color: [57, 255, 198] },
  PERCEIVING: { label: "视觉扫描中", code: "PERCEIVING // VISION", color: [70, 165, 255] },
  GUARD:      { label: "等待安全确认", code: "GUARD // AUTHORIZATION", color: [255, 77, 109] },
};
const VOICE_MODES = [
  { key: "yunxi", label: "云希", tts_voice: "云希", robot_voice: 0 },
  { key: "yunyang", label: "云扬", tts_voice: "云扬", robot_voice: 0 },
  { key: "robot", label: "机械音", tts_voice: null, robot_voice: .7 },
];

const S = {
  state: "IDLE",
  level: 0,
  smoothLevel: 0,
  speaking: false,
  subtitle: "",
  subtitleFinal: false,
  lyricKey: "",
  output: $("output-text").textContent,
  guard: null,
  bridgeReady: false,
  config: {
    providers: ["DeepSeek", "通义千问 (Qwen)", "智谱清言 (GLM)"],
    voices: ["云希", "云扬", "晓晓", "晓伊", "云健"],
    ai_provider: "DeepSeek",
    api_key: "",
    tts_voice: "云希",
    robot_voice: 0,
    vision_enabled: true,
    always_listen: true,
  },
  metrics: { cpu: 18, memory: 46, disk: 51.2 },
  metricView: { cpu: 18, memory: 46, disk: 51.2 },
  color: [0, 240, 255],
  logCount: 0,
  lastFrame: performance.now(),
  lastTelemetryPaint: 0,
  started: performance.now(),
  fps: 60,
};

function clamp(v, min = 0, max = 1) { return Math.max(min, Math.min(max, v)); }
function rgb(c, a = 1) { return `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`; }
function boolValue(value, fallback = true) {
  if (value === undefined || value === null) return fallback;
  if (typeof value === "string") return !/^(0|false|off|no|关闭|否)$/i.test(value.trim());
  return Boolean(value);
}
function api() { return window.pywebview && window.pywebview.api ? window.pywebview.api : null; }
function callBridge(method, ...args) {
  const bridge = api();
  if (!bridge || typeof bridge[method] !== "function") {
    showToast("PyWebView 桥接尚未就绪，请稍候再试", true);
    return Promise.resolve(false);
  }
  return Promise.resolve(bridge[method](...args)).catch((err) => {
    console.error(method, err);
    showToast("桥接调用失败，请查看 web-fullscreen.log", true);
    return false;
  });
}

let toastTimer = 0;
function showToast(message, error = false) {
  const el = $("toast");
  clearTimeout(toastTimer);
  el.textContent = String(message || "");
  el.classList.toggle("error", Boolean(error));
  el.classList.add("show");
  toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

/* ── Python → 页面唯一状态入口 ── */
window.JarvisHUD = {
  update(payload) {
    try {
      const data = typeof payload === "string" ? JSON.parse(payload) : payload;
      const snap = data.snap || {};
      S.bridgeReady = true;
      if (snap.state && STATE_META[snap.state]) setState(snap.state);
      if (Number.isFinite(snap.level)) S.level = clamp(snap.level);
      S.speaking = Boolean(snap.speaking);
      S.subtitle = String(snap.subtitle || "");
      S.subtitleFinal = Boolean(snap.subtitle_final);
      S.guard = snap.guard || null;
      updateSubtitle();
      updateLyric();
      updateGuard();

      if (typeof data.output === "string" && data.output.trim()) {
        S.output = data.output;
        $("output-text").textContent = data.output;
        $("output-scroll").scrollTop = 0;
      }
      if (data.config) updateConfig(data.config);
      if (data.flags) updateConfig(data.flags);
      if (data.metrics) {
        for (const key of ["cpu", "memory", "disk"]) {
          if (Number.isFinite(data.metrics[key])) S.metrics[key] = clamp(data.metrics[key], 0, 100);
        }
      }
      if (Array.isArray(data.logs) && data.logs.length) appendLogs(data.logs);
      if (data.notice && data.notice.message) showToast(data.notice.message, !data.notice.ok);
      $("bridge-status").textContent = "UI BRIDGE ONLINE";
      updateSelfCheck();
    } catch (err) {
      console.error("HUD update failed", err);
    }
  },
  setAnimating(active) {
    setAnimationActive(Boolean(active));
    return true;
  },
};

function setState(state) {
  if (S.state === state) return;
  S.state = state;
  document.body.dataset.state = state;
  const meta = STATE_META[state];
  $("state-label").textContent = meta.label;
  $("state-code").textContent = meta.code;
  updateSelfCheck();
}

function updateSubtitle() {
  const el = $("live-subtitle");
  if (S.subtitle) {
    el.textContent = `${S.speaking ? "语音输出" : "实时通道"} / ${S.subtitle}`;
    el.title = S.subtitle;
  } else {
    el.textContent = "语音与文字通道均已就绪";
    el.removeAttribute("title");
  }
}

let lyricTimer = 0;
function updateLyric() {
  const key = S.subtitleFinal && S.subtitle ? S.subtitle : "";
  if (key === S.lyricKey) return;
  S.lyricKey = key;
  const el = $("speech-lyric");
  if (!el) return;
  clearTimeout(lyricTimer);
  el.classList.remove("visible");
  if (!key) {
    el.textContent = "";
    return;
  }
  el.textContent = key;
  requestAnimationFrame(() => el.classList.add("visible"));
  const hold = Math.max(4500, Math.min(20000, key.length * 240));
  function fadeLyric() {
    if (S.speaking && S.subtitleFinal) {
      lyricTimer = setTimeout(fadeLyric, 1000);
      return;
    }
    el.classList.remove("visible");
  }
  lyricTimer = setTimeout(fadeLyric, hold);
}

function updateGuard() {
  const bar = $("guard-bar");
  bar.hidden = !S.guard;
  if (S.guard) $("guard-desc").textContent = S.guard.desc || S.subtitle || "请确认是否执行此操作";
}

/* ── 自检节点：只用真实链路状态，不伪造已连接 ── */
function setCheck(rowId, state, label) {
  const row = $(rowId);
  row.classList.remove("verified", "pending", "checking");
  row.classList.add(state);
  $(rowId.replace("check-", "") + "-state").textContent = label;
}
function updateSelfCheck() {
  setCheck("check-identity", "verified", "LOCKED");
  const audioReady = S.bridgeReady || (S.state === "LISTENING" && S.subtitleFinal);
  const audioMode = boolValue(S.config.always_listen) ? "READY" : "WAKE";
  setCheck("check-audio", audioReady ? "verified" : "pending", audioReady ? audioMode : "LINKING");
  const llmReady = Boolean(String(S.config.api_key || "").trim()) || S.state === "THINKING";
  setCheck("check-llm", llmReady ? "verified" : "pending", llmReady ? "CONNECTED" : "WAITING");

  const result = $("self-test-result");
  if (result) {
    const pass = audioReady && llmReady;
    result.textContent = pass ? "PASS" : "CHECK";
    result.style.color = pass ? "" : "var(--amber)";
  }
}

/* ── 配置 / 四联开关 / 声线循环 ── */
let providerSignature = "";
let wakeModeNoticeShown = false;
function voiceModeIndex() {
  if (Number(S.config.robot_voice) > 0) return 2;
  return String(S.config.tts_voice || "").trim() === "云扬" ? 1 : 0;
}
function updateConfig(config) {
  S.config = Object.assign({}, S.config, config);
  const providers = Array.isArray(S.config.providers) ? S.config.providers : [];
  const nextProviderSignature = providers.join("|");
  if (nextProviderSignature && nextProviderSignature !== providerSignature) {
    providerSignature = nextProviderSignature;
    const select = $("provider");
    select.replaceChildren();
    for (const name of providers) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      select.appendChild(option);
    }
  }
  if (S.config.ai_provider) $("provider").value = S.config.ai_provider;
  if (document.activeElement !== $("api-key") && typeof S.config.api_key === "string") {
    $("api-key").value = S.config.api_key;
  }
  renderStateSwitch("vision", boolValue(S.config.vision_enabled));
  renderStateSwitch("auto", boolValue(S.config.always_listen));
  renderVoiceMode();
  updateSelfCheck();
  if (!boolValue(S.config.always_listen) && !wakeModeNoticeShown) {
    wakeModeNoticeShown = true;
    setTimeout(() => showToast("当前为唤醒词模式：语音仍在线，请先说“贾维斯”或“星光”"), 700);
  }
}
function renderStateSwitch(key, enabled) {
  const button = $(key + "-toggle");
  const label = $(key + "-state");
  button.classList.toggle("off", !enabled);
  button.setAttribute("aria-checked", String(enabled));
  // 自动接管关闭不是故障，而是退回唤醒词模式；用 WAKE 明确区分于普通 OFF。
  label.textContent = enabled ? "ON" : (key === "auto" ? "WAKE" : "OFF");
  if (key === "auto") {
    button.title = enabled
      ? "免唤醒直接听指令；关闭后需先说“贾维斯”或“星光”"
      : "当前为唤醒词模式：先说“贾维斯”或“星光”再下指令";
    button.setAttribute("aria-label", enabled ? "自动接管已开启，免唤醒直接接收指令" : "自动接管已关闭，当前为唤醒词模式");
  }
}
function renderVoiceMode() {
  const modes = $("voice-modes").querySelectorAll("[data-voice-mode]");
  const current = voiceModeIndex();
  const activeKey = VOICE_MODES[current].key;
  modes.forEach((node) => node.classList.toggle("active", node.dataset.voiceMode === activeKey));

  const robotOn = Number(S.config.robot_voice) > 0;
  const tts = String(S.config.tts_voice || "云希");
  if (robotOn) $("voice-state").textContent = "机械音";
  else if (tts === "云扬") $("voice-state").textContent = "云扬";
  else $("voice-state").textContent = tts || "云希";
  const voiceModule = $("voice-cycle");
  voiceModule.setAttribute("aria-label", `当前声线${$("voice-state").textContent}，点击循环切换`);
}

/* ── Canvas 管理 ── */
function surface(id) {
  const canvas = $(id);
  return { canvas, ctx: canvas.getContext("2d"), w: 0, h: 0, dpr: 1 };
}
function fit(s) {
  if (!s.ctx) return false;
  const rect = s.canvas.getBoundingClientRect();
  const w = Math.max(1, rect.width);
  const h = Math.max(1, rect.height);
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  if (Math.abs(w - s.w) > .5 || Math.abs(h - s.h) > .5 || dpr !== s.dpr) {
    s.w = w; s.h = h; s.dpr = dpr;
    s.canvas.width = Math.round(w * dpr);
    s.canvas.height = Math.round(h * dpr);
    s.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  return true;
}
const reactor = surface("reactor");
const waveformLeft = surface("waveform-left");
const waveformRight = surface("waveform-right");
const telemetry = surface("telemetry");
const spectrum = surface("spectrum");

const particles = Array.from({ length: 64 }, (_, i) => ({
  angle: Math.random() * TAU,
  radius: .48 + Math.random() * .72,
  y: (Math.random() - .5) * 1.15,
  speed: (.08 + Math.random() * .19) * (i % 2 ? 1 : -1),
  size: .45 + Math.random() * 1.35,
  phase: Math.random() * TAU,
}));

/* 离屏缓存：静态光晕/刻度环/粒子贴图只渲染一次，主循环只做贴图与少量动态层 */
const FX = {
  bg: document.createElement("canvas"), bgKey: "", bgStamp: 0,
  ticks: document.createElement("canvas"), tickKey: "", tickStamp: 0,
  particle: document.createElement("canvas"), particleKey: "", particleStamp: 0,
};
function colorKey(color) {
  return `${Math.round(color[0] / 6) * 6},${Math.round(color[1] / 6) * 6},${Math.round(color[2] / 6) * 6}`;
}
function ensureBackground(w, h, dpr, cx, cy, R, color, now) {
  const key = `${Math.round(w)}x${Math.round(h)}@${dpr}|${Math.round(R)}|${colorKey(color)}`;
  if (key === FX.bgKey) return;
  if (FX.bgKey && now - FX.bgStamp < 150) return;
  const layer = FX.bg;
  layer.width = Math.max(1, Math.round(w * dpr));
  layer.height = Math.max(1, Math.round(h * dpr));
  const g = layer.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const aura = g.createRadialGradient(cx, cy, 0, cx, cy, R * 1.62);
  aura.addColorStop(0, rgb(color, .16));
  aura.addColorStop(.35, rgb(color, .055));
  aura.addColorStop(1, rgb(color, 0));
  g.fillStyle = aura;
  g.fillRect(cx - R * 1.72, cy - R * 1.72, R * 3.44, R * 3.44);
  g.save();
  g.globalAlpha = .32;
  g.strokeStyle = rgb(color, .18);
  g.lineWidth = .7;
  g.beginPath(); g.ellipse(cx, cy, R * 1.14, R * 1.14 * .74, 0, 0, TAU); g.stroke();
  g.setLineDash([2, 8]);
  g.strokeStyle = rgb(color, .22);
  g.beginPath(); g.ellipse(cx, cy, R * .92, R * .92 * .74, 0, 0, TAU); g.stroke();
  g.setLineDash([]);
  g.strokeStyle = rgb(color, .11);
  g.beginPath();
  g.moveTo(cx - R * 1.28, cy); g.lineTo(cx - R * .96, cy);
  g.moveTo(cx + R * .96, cy); g.lineTo(cx + R * 1.28, cy);
  g.moveTo(cx, cy - R * .92); g.lineTo(cx, cy - R * .74);
  g.moveTo(cx, cy + R * .74); g.lineTo(cx, cy + R * .92);
  g.stroke();
  g.restore();

  // Arwes 式仪表底盘：极淡极坐标网格 + 长十字基准线，缓存一次，不进主循环
  g.save();
  g.translate(cx, cy);
  g.scale(1, .74);
  g.lineWidth = .6;
  g.strokeStyle = rgb(color, .075);
  for (const rr of [R * .34, R * .52, R * .70, R * .88, R * 1.04]) {
    g.beginPath(); g.arc(0, 0, rr, 0, TAU); g.stroke();
  }
  for (let i = 0; i < 12; i++) {
    const a = i * TAU / 12;
    g.beginPath();
    g.moveTo(Math.cos(a) * R * .30, Math.sin(a) * R * .30);
    g.lineTo(Math.cos(a) * R * 1.06, Math.sin(a) * R * 1.06);
    g.stroke();
  }
  g.setLineDash([2, 8]);
  g.strokeStyle = rgb(color, .13);
  g.beginPath(); g.arc(0, 0, R * .43, 0, TAU); g.stroke();
  g.setLineDash([]);
  g.strokeStyle = rgb(color, .10);
  g.beginPath();
  g.moveTo(-R * 1.32, 0); g.lineTo(-R * 1.06, 0);
  g.moveTo(R * 1.06, 0); g.lineTo(R * 1.32, 0);
  g.moveTo(0, -R * 1.12); g.lineTo(0, -R * .94);
  g.moveTo(0, R * .94); g.lineTo(0, R * 1.12);
  g.stroke();
  g.restore();

  FX.bgKey = key;
  FX.bgStamp = now;
}
function ensureTickLayer(R, color, now) {
  const key = `${Math.round(R)}|${colorKey(color)}`;
  if (key === FX.tickKey || now - FX.tickStamp < 110) return;
  const layer = FX.ticks;
  const scale = 1.5;
  const span = R * 2.32;
  layer.width = Math.max(1, Math.round(span * scale));
  layer.height = layer.width;
  const g = layer.getContext("2d");
  g.setTransform(scale, 0, 0, scale, 0, 0);
  g.clearRect(0, 0, span, span);
  const center = span / 2;
  g.translate(center, center);
  g.shadowColor = rgb(color, .8);
  for (let i = 0; i < 128; i++) {
    const major = i % 10 === 0;
    const medium = i % 5 === 0;
    const a = i * TAU / 128;
    const r1 = R * (major ? .965 : medium ? 1.005 : 1.022);
    const r2 = R * 1.068;
    g.beginPath();
    g.moveTo(Math.cos(a) * r1, Math.sin(a) * r1 * .74);
    g.lineTo(Math.cos(a) * r2, Math.sin(a) * r2 * .74);
    g.strokeStyle = rgb(color, major ? .9 : medium ? .48 : .2);
    g.lineWidth = major ? 1.8 : .8;
    g.shadowBlur = major ? 10 : 0;
    g.stroke();
  }
  FX.tickKey = key;
  FX.tickStamp = now;
}
function ensureParticleSprite(color, now) {
  const key = colorKey(color);
  if (key === FX.particleKey || now - FX.particleStamp < 110) return;
  const layer = FX.particle;
  layer.width = 24;
  layer.height = 24;
  const g = layer.getContext("2d");
  g.clearRect(0, 0, 24, 24);
  const glow = g.createRadialGradient(12, 12, 0, 12, 12, 12);
  glow.addColorStop(0, "rgba(255,255,255,.96)");
  glow.addColorStop(.22, rgb(color, .95));
  glow.addColorStop(.55, rgb(color, .35));
  glow.addColorStop(1, rgb(color, 0));
  g.fillStyle = glow;
  g.fillRect(0, 0, 24, 24);
  FX.particleKey = key;
  FX.particleStamp = now;
}
function ellipseArc(ctx, cx, cy, radius, flatten, start, end, color, width, glow = 0) {
  ctx.beginPath();
  ctx.ellipse(cx, cy, radius, radius * flatten, 0, start, end);
  ctx.strokeStyle = color;
  if (glow) {
    ctx.globalAlpha = .18;
    ctx.lineWidth = width + 3;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  ctx.lineWidth = width;
  ctx.stroke();
}

function drawReactor(now) {
  if (!fit(reactor)) return;
  const ctx = reactor.ctx;
  const w = reactor.w, h = reactor.h;
  if (w < 3 || h < 3) return;
  const t = (now - S.started) / 1000;
  const cx = w / 2, cy = h * .47;
  const R = Math.min(w * .42, h * .41, 220);
  const color = S.color;
  const lv = S.smoothLevel;
  ctx.clearRect(0, 0, w, h);

  ensureBackground(w, h, reactor.dpr, cx, cy, R, color, now);
  ctx.drawImage(FX.bg, 0, 0, w, h);

  // 纯光叠加：硬边刻线、雷达扫掠和粒子全部用 lighter 叠加。
  ctx.save();
  ctx.globalCompositeOperation = "lighter";

  ensureTickLayer(R, color, now);
  ensureParticleSprite(color, now);

  // 1) 外层刻度环：缓慢正转
  const tickSpan = R * 2.32;
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(t * .07);
  ctx.scale(1, .742);
  ctx.globalAlpha = .92;
  ctx.drawImage(FX.ticks, -tickSpan / 2, -tickSpan / 2, tickSpan, tickSpan);
  ctx.restore();

  // 2) 反向虚线轨道：Arwes 式“机械仪表”骨架
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(-t * .15);
  ctx.scale(1, .742);
  ctx.setLineDash([3, 9]);
  ctx.lineWidth = 1;
  ctx.strokeStyle = rgb(color, .32);
  ctx.beginPath(); ctx.arc(0, 0, R * 1.13, 0, TAU); ctx.stroke();
  ctx.setLineDash([1, 4]);
  ctx.strokeStyle = rgb(color, .16);
  ctx.beginPath(); ctx.arc(0, 0, R * 1.19, 0, TAU); ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();

  // 3) 雷达扫掠：旋转的锥形光扇，只做一次渐变填充
  if (typeof ctx.createConicGradient === "function") {
    const sweep = ctx.createConicGradient(t * .46, cx, cy);
    sweep.addColorStop(0, rgb(color, 0));
    sweep.addColorStop(.012, rgb(color, .38));
    sweep.addColorStop(.11, rgb(color, .075));
    sweep.addColorStop(.32, rgb(color, 0));
    sweep.addColorStop(1, rgb(color, 0));
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(1, .742);
    ctx.globalAlpha = .92;
    ctx.fillStyle = sweep;
    ctx.beginPath(); ctx.arc(0, 0, R * 1.05, 0, TAU); ctx.fill();
    ctx.restore();

    // 扫掠前缘光束，让旋转方向一眼可读
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(1, .742);
    ctx.rotate(t * .46);
    ctx.strokeStyle = rgb(color, .34);
    ctx.lineWidth = 1.1;
    ctx.beginPath();
    ctx.moveTo(R * .34, 0);
    ctx.lineTo(R * 1.06, 0);
    ctx.stroke();
    ctx.restore();
  }

  // 4) 周期性扫描环：从内核向外扩散，强化纵深感
  const scanP = (t * .34) % 1;
  ctx.save();
  ctx.globalAlpha = (1 - scanP) * .48;
  ctx.setLineDash([1, 5]);
  ctx.strokeStyle = rgb(color, .72);
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.ellipse(cx, cy, R * (.35 + .78 * scanP), R * (.35 + .78 * scanP) * .742, 0, 0, TAU);
  ctx.stroke();
  ctx.restore();

  // 5) 分段主环：比整圈更接近 Arwes 的断线框架
  const spin = t * (S.state === "THINKING" ? 1.05 : .32);
  for (let i = 0; i < 8; i++) {
    const a = i * TAU / 8 - spin;
    ellipseArc(ctx, cx, cy, R * .88, .742, a, a + .33, rgb(color, .72), 1.7, 12);
  }
  for (let i = 0; i < 5; i++) {
    const a = i * TAU / 5 + spin * .63;
    ellipseArc(ctx, cx, cy, R * .73, .742, a + .12, a + .56, rgb(color, .34), 1, 6);
  }

  // 6) 内层三维轨道
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(.34 + Math.sin(t * .23) * .18);
  ellipseArc(ctx, 0, 0, R * .61, .29, 0, TAU, rgb(color, .44), 1.15, 8);
  ctx.rotate(-1.12 + t * .11);
  ellipseArc(ctx, 0, 0, R * .53, .35, 0, TAU, rgb(color, .58), 1.25, 10);
  ctx.restore();

  // 7) 横向扫描束：只在反应堆范围内可见，像 CRT 读取线
  const scanY = cy + Math.sin(t * .82) * R * .66;
  const scanGrad = ctx.createLinearGradient(cx - R * 1.1, 0, cx + R * 1.1, 0);
  scanGrad.addColorStop(0, rgb(color, 0));
  scanGrad.addColorStop(.24, rgb(color, .10));
  scanGrad.addColorStop(.5, rgb(color, .38));
  scanGrad.addColorStop(.76, rgb(color, .10));
  scanGrad.addColorStop(1, rgb(color, 0));
  ctx.save();
  ctx.globalAlpha = .68;
  ctx.strokeStyle = scanGrad;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(cx - R * 1.1, scanY);
  ctx.lineTo(cx + R * 1.1, scanY);
  ctx.stroke();
  ctx.restore();

  // 8) 粒子星云
  ctx.save();
  for (const p of particles) {
    const a = p.angle + t * p.speed * (S.state === "THINKING" ? 2.4 : 1);
    const x = Math.cos(a) * p.radius * R;
    const z = Math.sin(a) * p.radius * R;
    const y0 = (p.y + Math.sin(t * .3 + p.phase) * .035) * R;
    const tilt = .46;
    const y = y0 * Math.cos(tilt) - z * Math.sin(tilt);
    const zz = y0 * Math.sin(tilt) + z * Math.cos(tilt);
    const perspective = (R * 3) / (R * 3 + zz);
    const px = cx + x * perspective;
    const py = cy + y * perspective;
    const depth = clamp(1 - (zz / R + 1) * .32, .22, 1);
    const size = Math.max(2.2, p.size * perspective * 5.2);
    ctx.globalAlpha = depth * (.32 + .52 * Math.abs(Math.sin(t * 1.3 + p.phase)));
    ctx.drawImage(FX.particle, px - size / 2, py - size / 2, size, size);
  }
  ctx.restore();

  // 9) 内核：光球 + 六边形硬边 + 流体声纹
  const speakingBoost = S.speaking ? 1.15 : 1;
  const coreR = R * (.245 + lv * .065 * speakingBoost + Math.sin(t * 2.8) * .008);
  ctx.save();
  const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 1.9);
  glow.addColorStop(0, "rgba(255,255,255,.98)");
  glow.addColorStop(.14, rgb(color, .98));
  glow.addColorStop(.42, rgb(color, .36));
  glow.addColorStop(1, rgb(color, 0));
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(cx, cy, coreR * 1.9, 0, TAU);
  ctx.fill();

  // 六边形外框，让“能量核心”有机械结构而不是一团雾
  ctx.beginPath();
  for (let i = 0; i < 6; i++) {
    const a = i * TAU / 6 - Math.PI / 6 + t * .18;
    const rr = coreR * 1.16;
    const px = cx + Math.cos(a) * rr;
    const py = cy + Math.sin(a) * rr * .94;
    if (i) ctx.lineTo(px, py); else ctx.moveTo(px, py);
  }
  ctx.closePath();
  ctx.strokeStyle = "rgba(225, 255, 255, .58)";
  ctx.globalAlpha = .7;
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.globalAlpha = 1;

  for (let layer = 0; layer < 2; layer++) {
    ctx.beginPath();
    const points = 96;
    for (let i = 0; i <= points; i++) {
      const a = i * TAU / points;
      const wave = Math.sin(a * 7 + t * 5.4) * .52 + Math.sin(a * 3 - t * 3.1) * .3;
      const rr = coreR * (1.02 + layer * .19) + wave * (2 + lv * 19) * speakingBoost;
      const x = cx + Math.cos(a) * rr;
      const y = cy + Math.sin(a) * rr;
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    }
    ctx.closePath();
    ctx.strokeStyle = layer ? rgb(color, .35) : "rgba(255,255,255,.86)";
    ctx.lineWidth = layer ? 1 : 1.35;
    ctx.globalAlpha = .2;
    ctx.lineWidth = layer ? 2.6 : 3.2;
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.lineWidth = layer ? 1 : 1.35;
    ctx.stroke();
  }

  // 内核中心“J”临时切回 source-over，保持字母边缘干净不发糊
  ctx.fillStyle = "rgba(235,255,255,.96)";
  ctx.shadowColor = rgb(color, .92);
  ctx.shadowBlur = 16;
  ctx.globalCompositeOperation = "source-over";
  ctx.font = `600 ${Math.max(22, coreR * .74)}px "JetBrains Mono", Consolas, monospace`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("J", cx, cy + 1);
  ctx.restore();
  ctx.restore();
}

function drawWaveform(s, mirrored) {
  if (!fit(s)) return;
  const ctx = s.ctx, w = s.w, h = s.h;
  ctx.clearRect(0, 0, w, h);
  ctx.globalCompositeOperation = "lighter";
  const count = Math.max(28, Math.min(62, Math.floor(w / 9)));
  const step = w / count;
  const t = (performance.now() - S.started) / 1000;
  const phase = mirrored ? 1.9 : 0;
  ctx.fillStyle = rgb(S.color, .86);
  ctx.globalAlpha = .22;
  for (let i = 0; i < count; i++) {
    const centerDistance = Math.abs(i - count / 2) / (count / 2);
    const phaseIndex = mirrored ? count - i : i;
    const carrier = .32 + .68 * Math.abs(Math.sin(t * 7.2 + phaseIndex * .47 + phase));
    const height = 4 + (3 + S.smoothLevel * (h - 14)) * carrier * (1.06 - centerDistance * .42);
    const x = i * step + step * .35;
    ctx.fillRect(x - 1, (h - height) / 2, Math.max(1, step * .24) + 2, height);
  }
  ctx.globalAlpha = 1;
  for (let i = 0; i < count; i++) {
    const centerDistance = Math.abs(i - count / 2) / (count / 2);
    const phaseIndex = mirrored ? count - i : i;
    const carrier = .32 + .68 * Math.abs(Math.sin(t * 7.2 + phaseIndex * .47 + phase));
    const height = 4 + (3 + S.smoothLevel * (h - 14)) * carrier * (1.06 - centerDistance * .42);
    const x = i * step + step * .35;
    ctx.globalAlpha = .3 + .7 * (1 - centerDistance);
    ctx.fillRect(x, (h - height) / 2, Math.max(1, step * .24), height);
  }
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "source-over";
  if (!mirrored) $("wave-level").textContent = String(Math.round(S.smoothLevel * 100)).padStart(3, "0");
}

const GAUGES = [
  { key: "cpu", label: "CPU 负载", code: "CPU", color: "#00F0FF" },
  { key: "memory", label: "内存使用", code: "MEM", color: "#2A9DFF" },
  { key: "disk", label: "主盘空间", code: "DSK", color: "#39FFC6" },
];
function drawTelemetry(now) {
  if (!fit(telemetry)) return;
  if (now - S.lastTelemetryPaint < 180) return;
  S.lastTelemetryPaint = now;
  const ctx = telemetry.ctx, w = telemetry.w, h = telemetry.h;
  ctx.clearRect(0, 0, w, h);
  ctx.globalCompositeOperation = "lighter";
  const cell = w / 3;
  const radius = Math.min(50, cell * .32, h * .28);
  const cy = Math.min(h * .40, 72);
  GAUGES.forEach((gauge, index) => {
    const value = S.metricView[gauge.key];
    const cx = cell * (index + .5);
    const segments = 42;
    for (let i = 0; i < segments; i++) {
      const active = i / segments <= value / 100;
      const a = Math.PI * .75 + i / (segments - 1) * Math.PI * 1.5;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * (radius - 4), cy + Math.sin(a) * (radius - 4));
      ctx.lineTo(cx + Math.cos(a) * radius, cy + Math.sin(a) * radius);
      ctx.strokeStyle = active ? gauge.color : "rgba(54,92,112,.2)";
      ctx.lineWidth = active ? 2 : 1;
      ctx.stroke();
    }
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, radius - 10, 0, TAU);
    ctx.strokeStyle = "rgba(68,133,158,.11)";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, radius - 13, Math.PI * .75, Math.PI * .75 + Math.PI * 1.5 * value / 100);
    ctx.strokeStyle = gauge.color;
    ctx.globalAlpha = .35;
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = gauge.color;
    ctx.font = "500 20px \"JetBrains Mono\", Consolas, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(`${value.toFixed(1)}%`, cx, cy - 1);
    ctx.fillStyle = "rgba(224,247,255,.96)";
    ctx.font = "14px \"Microsoft YaHei UI\", sans-serif";
    ctx.fillText(gauge.label, cx, cy + radius + 18);
    ctx.fillStyle = "rgba(139,183,201,.9)";
    ctx.font = "14px \"JetBrains Mono\", Consolas, monospace";
    ctx.fillText(`${gauge.code} // 0—100`, cx, cy + radius + 35);
  });
  ctx.globalCompositeOperation = "source-over";
}

function drawSpectrum(now) {
  if (!fit(spectrum)) return;
  const ctx = spectrum.ctx, w = spectrum.w, h = spectrum.h;
  ctx.clearRect(0, 0, w, h);
  ctx.globalCompositeOperation = "lighter";
  const count = Math.max(24, Math.min(44, Math.floor(w / 7)));
  const gap = 2;
  const bw = (w - 8 - gap * (count - 1)) / count;
  const t = now / 1000;
  const level = .18 + S.smoothLevel * .75;

  ctx.strokeStyle = "rgba(0,240,255,.06)";
  ctx.lineWidth = 1;
  for (let row = 1; row < 4; row++) {
    const y = h * (row / 4);
    ctx.beginPath(); ctx.moveTo(4, y); ctx.lineTo(w - 4, y); ctx.stroke();
  }

  const grad = ctx.createLinearGradient(0, h, 0, 0);
  grad.addColorStop(0, "rgba(0,240,255,.18)");
  grad.addColorStop(.52, "rgba(24,211,226,.75)");
  grad.addColorStop(1, "#7dffff");
  ctx.fillStyle = grad;
  let peak = 0;
  for (let i = 0; i < count; i++) {
    const envelope = Math.sin(Math.PI * (i / (count - 1))) ** .7;
    const motion = .17 + .83 * Math.abs(Math.sin(t * 2.2 + i * .36) * Math.sin(t * .83 + i * .19));
    const value = clamp(level * envelope * motion + .035);
    peak = Math.max(peak, value);
    const bh = Math.max(2, value * (h - 7));
    ctx.fillRect(4 + i * (bw + gap), h - 3 - bh, bw, bh);
  }
  ctx.globalCompositeOperation = "source-over";
  $("spectrum-peak").textContent = `-${Math.round(56 - peak * 28)} dB`;
}

/* ── 日志终端 ── */
function appendLogs(lines) {
  const stream = $("log-stream");
  const nearBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 70;
  for (const raw of lines) {
    const text = String(raw || "").trimEnd();
    if (!text.trim()) continue;
    const row = document.createElement("div");
    const lower = text.toLowerCase();
    row.className = "log-line" + (/error|失败|异常|traceback/.test(lower) ? " error" : /ai|模型|密钥/.test(lower) ? " ai" : /state|状态|界面|系统|jarvis/.test(lower) ? " state" : "");
    const time = document.createElement("time");
    const content = document.createElement("span");
    const match = text.match(/^(\d{2}:\d{2}:\d{2})\s+(.*)$/s);
    time.textContent = match ? match[1] : new Date().toLocaleTimeString("zh-CN", { hour12: false });
    content.textContent = match ? match[2] : text;
    row.append(time, content);
    stream.appendChild(row);
    S.logCount++;
  }
  while (stream.children.length > 260) stream.firstElementChild.remove();
  $("log-count").textContent = `${String(S.logCount).padStart(3, "0")} ENTRIES`;
  if (nearBottom) stream.scrollTop = stream.scrollHeight;
}

/* ── 3D 鼠标微视差：±3°，指数平滑，不做生硬跟随 ── */
const dashboard = $("dashboard");
const tilt = { x: 0, y: 0, tx: 0, ty: 0 };
function pointerMove(event) {
  if (REDUCED || event.pointerType === "touch") return;
  const nx = clamp(event.clientX / Math.max(1, innerWidth), 0, 1) - .5;
  const ny = clamp(event.clientY / Math.max(1, innerHeight), 0, 1) - .5;
  tilt.ty = nx * 6;
  tilt.tx = -ny * 5.2;
}
function pointerReset() { tilt.tx = 0; tilt.ty = 0; }
if (!REDUCED) {
  // 监听 window 而不是舞台：舞台已设为 pointer-events:none，避免 3D 容器抢点击。
  window.addEventListener("pointermove", pointerMove, { passive: true });
  document.addEventListener("pointerleave", pointerReset);
  window.addEventListener("blur", pointerReset);
}

/* ── 页头控制与侧翼折叠 ── */
function setWingsFolded(folded, announce = false) {
  document.body.classList.toggle("wings-folded", folded);
  const button = $("fold-wings");
  button.setAttribute("aria-expanded", String(!folded));
  $("fold-label").textContent = folded ? "展开 3D 侧翼" : "折叠 3D 侧翼";
  try { localStorage.setItem("jarvisWingsFolded", folded ? "1" : "0"); } catch (_) {}
  if (announce) showToast(folded ? "已折叠左右 3D 侧翼，仅保留中央战术魔盒" : "左右 3D 环绕翼已展开");
}
let wingsFolded = false;
try { wingsFolded = localStorage.getItem("jarvisWingsFolded") === "1"; } catch (_) {}
setWingsFolded(wingsFolded, false);

$("fold-wings").addEventListener("click", () => {
  wingsFolded = !document.body.classList.contains("wings-folded");
  setWingsFolded(wingsFolded, true);
});
$("collapse").addEventListener("click", () => callBridge("collapse"));
$("hide-all").addEventListener("click", () => callBridge("hide"));
$("guard-ok").addEventListener("click", () => callBridge("resolve_guard", true));
$("guard-no").addEventListener("click", () => callBridge("resolve_guard", false));

/* ── 快捷动作与指令 ── */
function sendCommand(text) {
  const command = String(text || "").trim();
  if (!command) return;
  callBridge("command", command);
  showToast(`指令已发送：${command}`);
}
$("command-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("command-input");
  const text = input.value.trim();
  if (!text) { showToast("请先输入要执行的指令"); return; }
  input.value = "";
  sendCommand(text);
});
document.querySelectorAll("[data-command]").forEach((button) => {
  button.addEventListener("click", () => sendCommand(button.dataset.command));
});

/* 四联魔盒：视觉感知 / 自动接管 / 声线 / 截屏锁屏 */
$("vision-toggle").addEventListener("click", () => {
  const next = !boolValue(S.config.vision_enabled);
  S.config.vision_enabled = next;
  renderStateSwitch("vision", next);
  showToast(next ? "视觉感知已开启：截图时将显示视觉扫描框" : "视觉感知已关闭：仅截图，不显示扫描框");
  callBridge("save_config", { vision_enabled: next });
});
$("auto-toggle").addEventListener("click", () => {
  const next = !boolValue(S.config.always_listen);
  S.config.always_listen = next;
  renderStateSwitch("auto", next);
  showToast(next ? "自动接管已开启：无需唤醒词即可接收指令" : "自动接管已关闭：语音仍在线，但需要先说“贾维斯”或“星光”唤醒");
  callBridge("save_config", { always_listen: next });
});
$("voice-cycle").addEventListener("click", () => {
  const next = VOICE_MODES[(voiceModeIndex() + 1) % VOICE_MODES.length];
  const patch = { robot_voice: next.robot_voice > 0 };
  if (next.tts_voice) {
    S.config.tts_voice = next.tts_voice;
    patch.tts_voice = next.tts_voice;
  }
  S.config.robot_voice = next.robot_voice;
  renderVoiceMode();
  const label = next.tts_voice ? next.label : `机械音 · ${S.config.tts_voice || "云希"}`;
  showToast(`声线已切换：${label}`);
  callBridge("save_config", patch);
});

/* AI 配置 */
$("provider").addEventListener("change", (event) => {
  callBridge("save_config", { ai_provider: event.target.value });
});
$("save-key").addEventListener("click", () => {
  callBridge("save_config", { api_key: $("api-key").value.trim() });
});
$("toggle-key").addEventListener("click", () => {
  const input = $("api-key");
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  $("toggle-key").setAttribute("aria-label", show ? "隐藏密钥" : "显示密钥");
});
$("clear-logs").addEventListener("click", () => {
  $("log-stream").replaceChildren();
  S.logCount = 0;
  $("log-count").textContent = "000 ENTRIES";
  showToast("日志视图已清空，不影响后端日志文件");
});

/* 键盘与桥接生命周期 */
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    if (S.guard) callBridge("resolve_guard", false);
    else callBridge("collapse");
  }
});
window.addEventListener("contextmenu", (event) => event.preventDefault());
window.addEventListener("pywebviewready", () => {
  S.bridgeReady = true;
  $("bridge-status").textContent = "UI BRIDGE READY";
  updateSelfCheck();
  callBridge("report", `${innerWidth}x${innerHeight}@${devicePixelRatio}`);
});

function updateClock() {
  $("sys-time").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const frequency = 47.82 + Math.sin(performance.now() / 2700) * .16;
  $("core-frequency").textContent = `${frequency.toFixed(3)} THz`;
  $("core-sync").textContent = (99.7 + Math.sin(performance.now() / 3100) * .25).toFixed(1);
}
updateClock();
setInterval(updateClock, 1000);

/* ── 统一动画循环：待命 30FPS，交互/推理 60FPS，隐藏立即停画 ── */
let fpsFrames = 0;
let fpsStamp = performance.now();
let lastReducedFrame = 0;
let rafHandle = 0;
let animating = !document.hidden;
let lastPaint = 0;
const ACTIVE_STATES = new Set(["THINKING", "ACTING", "PERCEIVING", "GUARD"]);

function scheduleFrame() {
  if (!animating || rafHandle) return;
  rafHandle = requestAnimationFrame(frame);
}
function setAnimationActive(active) {
  animating = Boolean(active) && !document.hidden && document.visibilityState !== "hidden";
  document.body.classList.toggle("anim-suspended", !animating);
  if (!animating) {
    if (rafHandle) cancelAnimationFrame(rafHandle);
    rafHandle = 0;
    return;
  }
  lastPaint = 0;
  S.lastFrame = performance.now();
  fpsFrames = 0;
  fpsStamp = performance.now();
  scheduleFrame();
}
function frame(now) {
  rafHandle = 0;
  if (!animating || document.visibilityState === "hidden") return;
  const busy = ACTIVE_STATES.has(S.state) || S.speaking || S.smoothLevel > .18;
  const targetFps = REDUCED ? 10 : busy ? 60 : 30;
  const interval = 1000 / targetFps;
  if (now - lastPaint < interval - 1.5) {
    scheduleFrame();
    return;
  }
  lastPaint = now;
  const dt = Math.min(.06, Math.max(.001, (now - S.lastFrame) / 1000));
  S.lastFrame = now;
  S.smoothLevel += (S.level - S.smoothLevel) * Math.min(1, dt * 13);

  const target = S.speaking ? STATE_META.IDLE.color : STATE_META[S.state].color;
  for (let i = 0; i < 3; i++) S.color[i] += (target[i] - S.color[i]) * Math.min(1, dt * 5.5);
  for (const key of ["cpu", "memory", "disk"]) {
    S.metricView[key] += (S.metrics[key] - S.metricView[key]) * Math.min(1, dt * 7);
  }
  for (const key of ["x", "y"]) tilt[key] += (tilt["t" + key] - tilt[key]) * Math.min(1, dt * 8);

  dashboard.style.setProperty("--parallax-x", `${tilt.x.toFixed(2)}deg`);
  dashboard.style.setProperty("--parallax-y", `${tilt.y.toFixed(2)}deg`);

  drawReactor(now);
  drawWaveform(waveformLeft, false);
  drawWaveform(waveformRight, true);
  drawTelemetry(now);
  drawSpectrum(now);

  fpsFrames++;
  if (now - fpsStamp > 750) {
    S.fps = Math.round(fpsFrames * 1000 / (now - fpsStamp));
    fpsFrames = 0;
    fpsStamp = now;
    $("fps-readout").textContent = `RENDER ${Math.min(99, S.fps)} FPS`;
  }
  scheduleFrame();
}

document.addEventListener("visibilitychange", () => setAnimationActive(!document.hidden));
window.addEventListener("pagehide", () => setAnimationActive(false));
window.addEventListener("pageshow", (event) => { if (!event.persisted) setAnimationActive(true); });
window.addEventListener("pywebviewready", () => setAnimationActive(true));

updateConfig(S.config);
scheduleFrame();

/* 仅用于无后端的本地 headless 视觉验收，不影响 PyWebView 正式流程 */
(function visualPreviewOnly() {
  if (window.pywebview || !new URLSearchParams(location.search).has("visual-preview")) return;
  const demo = {
    state: "IDLE", level: .12, subtitle: "全屏战术指挥大厅已就绪。Sir，随时听候您的差遣。",
    subtitle_final: true, speaking: false, guard: null,
  };
  window.JarvisHUD.update({
    snap: demo,
    config: Object.assign({}, S.config, { api_key: "demo-configured" }),
    output: "全屏战术指挥大厅已就绪。Sir，随时听候您的差遣。",
    metrics: { cpu: 18.4, memory: 58.5, disk: 61.2 },
    logs: ["BOOT  J.A.R.V.I.S. UI bridge initialized", "STATE  ->  IDLE // all systems nominal"],
    notice: null, preview: true,
  });
})();
