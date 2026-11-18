/* ============================================================
   贾维斯胶囊 · Canvas 粒子声纹核心 + 状态驱动
   ============================================================ */

const COLOR = {
  IDLE:       "#2E7F92",
  LISTENING:  "#00F0FF",
  THINKING:   "#FFB020",
  ACTING:     "#00FFA3",
  PERCEIVING: "#B06CFF",
  GUARD:      "#FF3B5C",
};

const LABEL = {
  IDLE:       ["待命", "SYSTEM ONLINE"],
  LISTENING:  ["聆听中", "正在收音 · 直接说指令"],
  THINKING:   ["思考中", "大模型推理中"],
  ACTING:     ["执行中", "正在控制电脑"],
  PERCEIVING: ["视觉扫描", "正在分析屏幕"],
  GUARD:      ["等待确认", "高危操作, 请确认"],
};

const cv = document.getElementById("core");
const ctx = cv.getContext("2d");
const titleEl = document.getElementById("title");
const tagEl = document.getElementById("tag");
const subEl = document.getElementById("sub");
const barsEl = document.getElementById("bars");

let W = 56, H = 56, DPR = window.devicePixelRatio || 1;

function fitCanvas() {
  const r = cv.getBoundingClientRect();
  W = Math.max(24, r.width);
  H = Math.max(24, r.height);
  DPR = window.devicePixelRatio || 1;
  cv.width = Math.round(W * DPR);
  cv.height = Math.round(H * DPR);
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
}

/* ── 状态 ── */
const S = {
  state: "IDLE",
  level: 0,          // 目标电平
  lv: 0,             // 平滑后电平
  sub: "系统在线",
  guard: null,
  t0: performance.now(),
  phase: 0,
};
const ACTIVE_STATES = new Set(["THINKING", "ACTING", "PERCEIVING", "GUARD"]);

function setMode(state) {
  const body = document.body;
  body.dataset.state = state;
  body.classList.remove("mode-dot", "mode-bar", "mode-card", "mode-guard");

  if (state === "IDLE") body.classList.add("mode-dot");
  else if (state === "GUARD") body.classList.add("mode-guard");
  else if (state === "THINKING" || state === "ACTING" || state === "PERCEIVING")
    body.classList.add("mode-card");
  else body.classList.add("mode-bar");

  fitCanvas();
}

/* ── 对外接口: Python 调用 ── */
window.JarvisUI = {
  update(payload) {
    try {
      const d = typeof payload === "string" ? JSON.parse(payload) : payload;
      const st = d.state || S.state;

      if (st !== S.state) {
        S.state = st;
        setMode(st);
        const meta = LABEL[st] || [st, ""];
        titleEl.textContent = meta[0];
        tagEl.textContent = st;
      }

      if (typeof d.level === "number") S.level = Math.max(0, Math.min(1, d.level));

      const hint = (LABEL[st] || ["", ""])[1];
      const sub = (d.subtitle || "").trim();
      if (sub) {
        S.sub = sub;
        subEl.textContent = sub;
        subEl.title = sub;
      } else if (subEl.textContent !== hint) {
        S.sub = "";
        subEl.textContent = hint;
        subEl.title = "";
      }

      S.guard = d.guard || null;
    } catch (e) {
      console.error("update failed", e);
    }
  },
  setAnimating(active) {
    setAnimationActive(Boolean(active));
    return true;
  },
};

/* ── 电平条 ── */
const BAR_N = 12;
const barEls = [];
for (let i = 0; i < BAR_N; i++) {
  const el = document.createElement("i");
  barsEl.appendChild(el);
  barEls.push(el);
}

/* ── 绘图 ── */
function ring(cx, cy, r, a0, a1, color, alpha, lw) {
  ctx.beginPath();
  ctx.arc(cx, cy, r, a0, a1);
  ctx.strokeStyle = color;
  ctx.globalAlpha = alpha * .22;
  ctx.lineWidth = lw + 2.2;
  ctx.stroke();
  ctx.globalAlpha = alpha;
  ctx.lineWidth = lw;
  ctx.stroke();
}

function fluidRing(cx, cy, r, t, amp, color, alpha) {
  ctx.beginPath();
  const N = 96;
  for (let i = 0; i <= N; i++) {
    const a = (i / N) * Math.PI * 2;
    const wobble = Math.sin(a * 5 + t * 3.1) * amp
                 + Math.sin(a * 3 - t * 2.1) * amp * 0.55;
    const rr = r + wobble;
    const x = cx + Math.cos(a) * rr;
    const y = cy + Math.sin(a) * rr;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  }
  ctx.closePath();
  ctx.strokeStyle = color;
  ctx.globalAlpha = alpha * .18;
  ctx.lineWidth = 3.2;
  ctx.stroke();
  ctx.globalAlpha = alpha;
  ctx.lineWidth = 1.4;
  ctx.stroke();
}

const dots = Array.from({ length: 9 }, (_, i) => ({
  a: Math.random() * Math.PI * 2,
  r: 0.62 + Math.random() * 0.3,
  spd: 0.25 + Math.random() * 0.6,
  size: 0.8 + Math.random() * 1.4,
}));

let capsuleRaf = 0;
let capsuleActive = !document.hidden;
let capsuleLastPaint = 0;
function scheduleCapsule() {
  if (!capsuleActive || capsuleRaf) return;
  capsuleRaf = requestAnimationFrame(draw);
}
function setAnimationActive(active) {
  capsuleActive = Boolean(active) && !document.hidden && document.visibilityState !== "hidden";
  document.body.classList.toggle("anim-suspended", !capsuleActive);
  if (!capsuleActive) {
    if (capsuleRaf) cancelAnimationFrame(capsuleRaf);
    capsuleRaf = 0;
    return;
  }
  capsuleLastPaint = 0;
  scheduleCapsule();
}

function draw(now) {
  capsuleRaf = 0;
  if (!capsuleActive || document.visibilityState === "hidden") return;
  const busyState = ACTIVE_STATES.has(S.state) || S.lv > .18;
  const targetFps = busyState ? 45 : 30;
  if (now - capsuleLastPaint < 1000 / targetFps - 1.5) {
    scheduleCapsule();
    return;
  }
  capsuleLastPaint = now;
  const t = (now - S.t0) / 1000;
  const st = S.state;
  const color = COLOR[st] || COLOR.IDLE;

  // 电平平滑 (带一点惯性, 让波形不死板)
  S.lv += (S.level - S.lv) * 0.18;
  const lv = S.lv;

  ctx.clearRect(0, 0, W, H);
  const cx = W / 2, cy = H / 2;
  const R = Math.min(W, H) / 2 - 3;

  const busy = st === "THINKING";
  const speed = busy ? 3.2 : st === "LISTENING" ? 1.5 : st === "IDLE" ? 0.5 : 1.1;

  // 1) 三层能量环
  ring(cx, cy, R * 0.96, t * speed * 0.7, t * speed * 0.7 + 1.7, color, 0.5, 1.6);
  ring(cx, cy, R * 0.78, -t * speed * 1.1, -t * speed * 1.1 + 1.1, color, 0.38, 1.2);
  ring(cx, cy, R * 0.60, t * speed * 1.7, t * speed * 1.7 + 0.6, color, 0.28, 1.0);

  // 2) 正弦流体波纹
  const amp = 1.2 + lv * 7 + (busy ? 1.6 * Math.sin(t * 6) : 0);
  fluidRing(cx, cy, R * 0.68, t, amp, color, 0.55);

  // 3) 内核光球
  const coreR = R * (0.30 + lv * 0.16) * (1 + 0.05 * Math.sin(t * (busy ? 9 : 4)));
  const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 1.9);
  g.addColorStop(0, "#ffffff");
  g.addColorStop(0.35, color);
  g.addColorStop(1, "rgba(0,0,0,0)");
  ctx.globalAlpha = 0.55 + lv * 0.4;
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(cx, cy, coreR * 1.9, 0, Math.PI * 2);
  ctx.fill();

  // 4) 轨道粒子
  for (const d of dots) {
    d.a += 0.004 * d.spd * (busy ? 3 : 1);
    const rr = R * d.r;
    const x = cx + Math.cos(d.a + t * 0.2) * rr;
    const y = cy + Math.sin(d.a + t * 0.2) * rr;
    ctx.globalAlpha = 0.35 + 0.45 * Math.abs(Math.sin(d.a * 3 + t));
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, d.size, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.globalAlpha = 1;

  // 5) 电平条（待命/确认态不可见时跳过，避免每帧写 12 个 DOM 样式）
  const active = st === "LISTENING" || st === "ACTING" || st === "PERCEIVING";
  const barsVisible = st !== "IDLE" && st !== "GUARD";
  if (barsVisible) {
    for (let i = 0; i < BAR_N; i++) {
      const ph = Math.sin(t * 6 + i * 0.55);
      const h = active
        ? 4 + lv * 26 * (0.55 + 0.45 * Math.abs(ph))
        : 4 + 3 * Math.abs(ph);
      barEls[i].style.height = h.toFixed(1) + "px";
      barEls[i].style.opacity = (active ? 0.55 + 0.45 * lv : 0.28).toFixed(2);
    }
  }

  scheduleCapsule();
}

/* ── 交互: 拖动 / 双击 / 确认 ── */
function api() { return (window.pywebview && window.pywebview.api) || null; }

let dragging = false, localDrag = false, lastX = 0, lastY = 0;

const shell = document.getElementById("shell");
shell.addEventListener("mousedown", (e) => {
  if (e.target.closest(".btn")) return;
  e.preventDefault();
  dragging = true;
  localDrag = false;
  lastX = e.screenX; lastY = e.screenY;
  document.body.style.cursor = "grabbing";
  // 正常情况由 Python 端接管拖动 (直接跟 GetCursorPos, 拖多快都不丢)
  const a = api();
  if (a && a.start_drag) {
    Promise.resolve(a.start_drag(e.clientX, e.clientY)).catch(() => { localDrag = true; });
  } else {
    localDrag = true;
  }
});
window.addEventListener("mousemove", (e) => {
  if (!dragging || !localDrag) return;   // Python 接管时这里完全不动, 避免位移翻倍
  const dx = e.screenX - lastX;
  const dy = e.screenY - lastY;
  if (!dx && !dy) return;
  lastX = e.screenX; lastY = e.screenY;
  const a = api();
  if (a && a.move_by) a.move_by(dx, dy);
});
window.addEventListener("mouseup", () => {
  dragging = false;
  localDrag = false;
  document.body.style.cursor = "default";
});

shell.addEventListener("dblclick", () => {
  const a = api();
  if (a && a.open_classic) a.open_classic();
});

document.getElementById("btn-ok").addEventListener("click", () => {
  const a = api();
  if (a && a.resolve_guard) a.resolve_guard(true);
});
document.getElementById("btn-no").addEventListener("click", () => {
  const a = api();
  if (a && a.resolve_guard) a.resolve_guard(false);
});

/* 屏蔽右键菜单 (用托盘菜单代替) */
window.addEventListener("contextmenu", (e) => e.preventDefault());

/* ── 启动 ── */
window.addEventListener("pywebviewready", () => {
  const a = api();
  if (a && a.ready) a.ready();
  if (a && a.report) {
    a.report(`${window.innerWidth}x${window.innerHeight}@${window.devicePixelRatio}`);
  }
});

setMode("IDLE");
fitCanvas();
document.addEventListener("visibilitychange", () => setAnimationActive(!document.hidden));
window.addEventListener("pagehide", () => setAnimationActive(false));
window.addEventListener("pageshow", (event) => { if (!event.persisted) setAnimationActive(true); });
window.addEventListener("pywebviewready", () => setAnimationActive(true));
scheduleCapsule();
