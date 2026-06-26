"use strict";

const $ = (s) => document.querySelector(s);
const api = async (m, url, body) => {
  const r = await fetch(url, {
    method: m,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.json();
};

let STATE = null;       // server state
let draftSteps = [];    // [{angles:{ch:deg}, move_time, hold}]

// ---- toast ----------------------------------------------------------------
let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 1800);
}

// ---- live control ---------------------------------------------------------
function renderServos() {
  const wrap = $("#servos");
  wrap.innerHTML = "";
  STATE.channels.forEach((ch) => {
    const angle = Math.round(STATE.angles[ch] ?? STATE.start_angle);
    const name = STATE.names[ch] || `Servo ${ch}`;
    const row = document.createElement("div");
    row.className = "servo";
    row.innerHTML = `
      <div class="name">${name} <span class="ch">ch ${ch}</span></div>
      <input type="range" min="${STATE.min_angle}" max="${STATE.max_angle}" value="${angle}" data-ch="${ch}" />
      <input class="val" type="number" min="${STATE.min_angle}" max="${STATE.max_angle}" value="${angle}" data-ch="${ch}" />`;
    wrap.appendChild(row);

    const range = row.querySelector("input[type=range]");
    const num = row.querySelector("input.val");
    const send = (v) => {
      v = Math.max(STATE.min_angle, Math.min(STATE.max_angle, Number(v)));
      range.value = v; num.value = v;
      STATE.angles[ch] = v;
      api("POST", `/api/servo/${ch}`, { angle: v }).catch((e) => toast("⚠ " + e.message));
    };
    range.addEventListener("input", () => { num.value = range.value; });
    range.addEventListener("change", () => send(range.value));
    range.addEventListener("input", () => { STATE.angles[ch] = Number(range.value); });
    num.addEventListener("change", () => send(num.value));
  });
}

$("#centerBtn").addEventListener("click", async () => {
  const s = await api("POST", "/api/center");
  STATE.angles = s.angles; renderServos(); toast("Centered");
});
$("#stopBtn").addEventListener("click", async () => {
  await api("POST", "/api/stop"); toast("Stopped"); refreshState();
});

// ---- sequence builder -----------------------------------------------------
function poseLabel(angles) {
  return STATE.channels
    .filter((ch) => angles[ch] !== undefined)
    .map((ch) => `${(STATE.names[ch] || ch).split(" ").slice(-1)}:${Math.round(angles[ch])}°`)
    .join("  ");
}

function renderSteps() {
  const box = $("#steps");
  box.innerHTML = "";
  $("#noSteps").classList.toggle("hidden", draftSteps.length > 0);
  draftSteps.forEach((st, i) => {
    const el = document.createElement("div");
    el.className = "step";
    el.innerHTML = `
      <div class="idx">${i + 1}</div>
      <div class="pose">${poseLabel(st.angles)}</div>
      <div class="timing">
        <label>move <input type="number" step="0.1" min="0" value="${st.move_time}" data-k="move_time" data-i="${i}" /></label>
        <label>hold <input type="number" step="0.1" min="0" value="${st.hold}" data-k="hold" data-i="${i}" /></label>
      </div>
      <div class="x" data-del="${i}" title="remove">✕</div>`;
    box.appendChild(el);
  });
  box.querySelectorAll("input[data-k]").forEach((inp) => {
    inp.addEventListener("change", () => {
      draftSteps[+inp.dataset.i][inp.dataset.k] = parseFloat(inp.value) || 0;
    });
  });
  box.querySelectorAll("[data-del]").forEach((b) => {
    b.addEventListener("click", () => { draftSteps.splice(+b.dataset.del, 1); renderSteps(); });
  });
}

$("#captureBtn").addEventListener("click", () => {
  const angles = {};
  STATE.channels.forEach((ch) => { angles[ch] = Math.round(STATE.angles[ch]); });
  draftSteps.push({ angles, move_time: 0.5, hold: 0.5 });
  renderSteps();
});
$("#clearBtn").addEventListener("click", () => {
  draftSteps = []; $("#seqName").value = ""; $("#loopChk").checked = false; renderSteps();
});

function draftSeq() {
  return {
    name: $("#seqName").value.trim() || "draft",
    loop: $("#loopChk").checked,
    steps: draftSteps,
  };
}

$("#runDraftBtn").addEventListener("click", async () => {
  if (!draftSteps.length) return toast("Add some steps first");
  await api("POST", "/api/run", draftSeq());
  toast("Running draft"); refreshState();
});
$("#saveBtn").addEventListener("click", async () => {
  const name = $("#seqName").value.trim();
  if (!name) return toast("Give it a name first");
  if (!draftSteps.length) return toast("No steps to save");
  await api("POST", "/api/sequences", draftSeq());
  toast(`Saved “${name}”`); loadSequences();
});

// ---- saved sequences ------------------------------------------------------
async function loadSequences() {
  const seqs = await api("GET", "/api/sequences");
  const box = $("#saved");
  box.innerHTML = "";
  const names = Object.keys(seqs).sort();
  $("#noSaved").classList.toggle("hidden", names.length > 0);

  // dropdown (keep current selection if still present)
  const sel = $("#seqSelect");
  const prev = sel.value;
  sel.innerHTML = "";
  names.forEach((name) => {
    const o = document.createElement("option");
    o.value = name;
    o.textContent = `${name} — ${seqs[name].steps.length} step(s)${seqs[name].loop ? " · loop" : ""}`;
    sel.appendChild(o);
  });
  if (names.includes(prev)) sel.value = prev;
  $(".quickrun").style.display = names.length ? "" : "none";

  names.forEach((name) => {
    const s = seqs[name];
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `
      <div>
        <b>${name}</b>
        <span class="meta">— ${s.steps.length} step(s)${s.loop ? " · loop" : ""}</span>
      </div>
      <div class="row-actions">
        <button class="btn small primary" data-run="${name}">▶ Run</button>
        <button class="btn small" data-edit="${name}">Edit</button>
        <button class="btn small ghost" data-del="${name}">Delete</button>
      </div>`;
    box.appendChild(row);
    row.querySelector("[data-run]").addEventListener("click", async () => {
      await api("POST", `/api/sequences/${encodeURIComponent(name)}/run`);
      toast(`Running “${name}”`); refreshState();
    });
    row.querySelector("[data-edit]").addEventListener("click", () => {
      draftSteps = JSON.parse(JSON.stringify(s.steps));
      $("#seqName").value = name; $("#loopChk").checked = !!s.loop; renderSteps();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
    row.querySelector("[data-del]").addEventListener("click", async () => {
      if (!confirm(`Delete “${name}”?`)) return;
      await api("DELETE", `/api/sequences/${encodeURIComponent(name)}`);
      toast(`Deleted “${name}”`); loadSequences();
    });
  });
}

// ---- quick run (dropdown) -------------------------------------------------
$("#quickRunBtn").addEventListener("click", async () => {
  const name = $("#seqSelect").value;
  if (!name) return toast("No sequence selected");
  await api("POST", `/api/sequences/${encodeURIComponent(name)}/run`);
  toast(`Running “${name}”`); refreshState();
});
$("#quickStopBtn").addEventListener("click", async () => {
  await api("POST", "/api/stop"); toast("Stopped"); refreshState();
});

// ---- AI control -----------------------------------------------------------
async function aiGenerate(run) {
  const prompt = $("#aiPrompt").value.trim();
  if (!prompt) return toast("Describe a motion first");
  const btns = [$("#aiGenBtn"), $("#aiRunBtn")];
  btns.forEach((b) => (b.disabled = true));
  toast("Asking Haiku…");
  try {
    const res = await api("POST", "/api/llm", { prompt, run });
    const seq = res.sequence;
    draftSteps = JSON.parse(JSON.stringify(seq.steps));
    $("#seqName").value = seq.name || "ai-sequence";
    $("#loopChk").checked = !!seq.loop;
    renderSteps();
    toast(run ? `Running “${seq.name}”` : `Built “${seq.name}” (${seq.steps.length} steps)`);
    if (run) refreshState();
  } catch (e) {
    toast("⚠ " + e.message);
  } finally {
    btns.forEach((b) => (b.disabled = false));
  }
}
$("#aiGenBtn").addEventListener("click", () => aiGenerate(false));
$("#aiRunBtn").addEventListener("click", () => aiGenerate(true));
$("#aiPrompt").addEventListener("keydown", (e) => { if (e.key === "Enter") aiGenerate(false); });

// ---- camera ---------------------------------------------------------------
let camShown = false;
function setCamera(on) {
  const wrap = $("#camWrap"), img = $("#camImg"), msg = $("#camMsg"), btn = $("#camToggle");
  if (on && STATE.camera) {
    img.src = "/camera/stream?ts=" + Date.now();
    wrap.classList.add("on");
    btn.textContent = "Hide";
    camShown = true;
  } else {
    img.src = "";
    wrap.classList.remove("on");
    msg.textContent = STATE.camera ? "camera off" : "no camera detected";
    btn.textContent = "Show";
    camShown = false;
  }
}
$("#camToggle").addEventListener("click", () => setCamera(!camShown));
$("#camImg").addEventListener("error", () => {
  if (camShown) { $("#camMsg").textContent = "stream error — retrying…"; }
});

// ---- state polling --------------------------------------------------------
function renderBadges() {
  const hw = $("#hwBadge");
  hw.textContent = STATE.mock ? "MOCK (no hardware)" : "LIVE";
  hw.className = "badge " + (STATE.mock ? "mock" : "live");
  const run = $("#runBadge");
  if (STATE.running) { run.classList.remove("hidden"); $("#runName").textContent = STATE.running; }
  else run.classList.add("hidden");
}

async function refreshState() {
  STATE = await api("GET", "/api/state");
  renderBadges();
}

let lastRunning = undefined;
async function poll() {
  try {
    const s = await api("GET", "/api/state");
    // only re-render sliders if a sequence is/was running (avoid fighting user drags)
    const wasRunning = lastRunning;
    lastRunning = s.running;
    STATE.running = s.running;
    STATE.mock = s.mock;
    renderBadges();
    if (s.running || wasRunning) {
      STATE.angles = s.angles;
      document.querySelectorAll(".servo").forEach((row) => {
        const ch = row.querySelector("input[type=range]").dataset.ch;
        const v = Math.round(s.angles[ch]);
        row.querySelector("input[type=range]").value = v;
        row.querySelector("input.val").value = v;
      });
    }
  } catch (_) {}
}

// ---- init -----------------------------------------------------------------
(async function init() {
  await refreshState();
  lastRunning = STATE.running;
  renderServos();
  renderSteps();
  await loadSequences();
  $("#aiCard").classList.toggle("hidden", !STATE.llm);
  $("#camCard").classList.toggle("hidden", !STATE.camera);
  setCamera(STATE.camera);   // auto-start feed when a camera is present
  setInterval(poll, 300);
})();
