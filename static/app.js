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
let robot = null;       // on-screen twin, mirrors whatever the servos are doing

function paintRobot(angles) {
  if (robot && angles) robot.setAngles(angles);
}

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
      paintRobot({ [ch]: v });
      api("POST", `/api/servo/${ch}`, { angle: v }).catch((e) => toast("⚠ " + e.message));
    };
    range.addEventListener("input", () => { num.value = range.value; });
    range.addEventListener("change", () => send(range.value));
    range.addEventListener("input", () => {
      STATE.angles[ch] = Number(range.value);
      paintRobot({ [ch]: Number(range.value) });   // follow the drag, not the poll
    });
    num.addEventListener("change", () => send(num.value));
  });
}

$("#centerBtn").addEventListener("click", async () => {
  const s = await api("POST", "/api/center");
  STATE.angles = s.angles; renderServos(); paintRobot(s.angles); toast("Centered");
});
$("#stopBtn").addEventListener("click", async () => {
  await api("POST", "/api/stop"); toast("Stopped"); refreshState();
});
let partyOn = false;
function updatePartyUI() {
  const b = $("#partyBtn");
  b.textContent = partyOn ? "🎉 Stop party" : "🎉 Party";
  b.classList.toggle("on", partyOn);
}
$("#partyBtn").addEventListener("click", async () => {
  try {
    const r = await api("POST", "/api/party", { on: !partyOn });
    partyOn = !!r.party; updatePartyUI();
    toast(partyOn ? "🎉 Party time!" : "Party stopped");
  } catch (e) { toast("⚠ " + e.message); }
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
  // (option text is set via textContent above, so names are never parsed as HTML)
  if (names.includes(prev)) sel.value = prev;
  $(".quickrun").style.display = names.length ? "" : "none";

  names.forEach((name) => {
    const s = seqs[name];
    const row = document.createElement("div");
    row.className = "row";
    // Names can come from the LLM or a voice transcript — never interpolate
    // them into innerHTML; set them as text after the markup is in place.
    row.innerHTML = `
      <div>
        <b></b>
        <span class="meta">— ${s.steps.length} step(s)${s.loop ? " · loop" : ""}</span>
      </div>
      <div class="row-actions">
        <button class="btn small primary" data-run>▶ Run</button>
        <button class="btn small" data-edit>Edit</button>
        <button class="btn small ghost" data-del>Delete</button>
      </div>`;
    row.querySelector("b").textContent = name;
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

// ---- voice log (heard / said) ---------------------------------------------
function addVoiceEntry(kind, text) {
  if (!text) return;
  const log = $("#voiceLog");
  $("#voiceLogEmpty").classList.add("hidden");
  const el = document.createElement("div");
  el.className = "vmsg " + kind;
  const who = kind === "heard" ? "🎤 heard" : "🔊 said";
  el.innerHTML = `<span class="who">${who}</span><span class="what"></span>`;
  el.querySelector(".what").textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
}
$("#voiceLogClear").addEventListener("click", () => {
  $("#voiceLog").innerHTML = "";
  $("#voiceLogEmpty").classList.remove("hidden");
});

// ---- conversation + face-tracking toggles ---------------------------------
let convOn = false, wakeOn = false, faceOn = false, lastConvN = 0;

function updateConvUI() {
  const b = $("#convToggle");
  b.textContent = "💬 Talk: " + (convOn ? "on" : "off");
  b.classList.toggle("on", convOn);
}
function updateWakeUI() {
  const b = $("#wakeToggle");
  b.textContent = "👂 Wake: " + (wakeOn ? "on" : "off");
  b.classList.toggle("on", wakeOn);
}
function applyConvStatus(c) {
  convOn = !!(c && c.on && c.mode === "always");
  wakeOn = !!(c && c.on && c.mode === "wake");
  updateConvUI(); updateWakeUI();
}
function updateFaceUI() {
  const b = $("#faceToggle");
  b.textContent = "🙂 Track: " + (faceOn ? "on" : "off");
  b.classList.toggle("on", faceOn);
}
function updateConvStatus(s) {
  const el = $("#convStatus"), bits = [];
  const c = s.conversation;
  if (c && c.on) {
    if (c.mode === "wake") bits.push(c.awake ? "👂 awake · " + c.status : "👂 say “Hello Abhishek”");
    else bits.push("💬 " + c.status);
  }
  if (s.face_track_on) bits.push(s.face_seen ? "🙂 face locked" : "🔎 searching…");
  el.textContent = bits.join("   ·   ");
  el.classList.toggle("hidden", bits.length === 0);
}
let langReady = false;
function setupLang(speech) {
  const sel = $("#langSelect");
  if (!langReady && speech && speech.languages) {
    sel.innerHTML = "";
    speech.languages.forEach((l) => {
      const o = document.createElement("option");
      o.value = l.id; o.textContent = l.label; sel.appendChild(o);
    });
    langReady = true;
  }
  if (speech && speech.current && document.activeElement !== sel) sel.value = speech.current;
}
$("#langSelect").addEventListener("change", async (e) => {
  try {
    const r = await api("POST", "/api/language", { id: e.target.value });
    toast("Language: " + (r.languages.find((l) => l.id === r.current) || {}).label);
  } catch (err) { toast("⚠ " + err.message); }
});
$("#convToggle").addEventListener("click", async () => {
  try {
    const r = await api("POST", "/api/conversation", { on: !convOn, wake: false });
    applyConvStatus(r);
    toast(convOn ? "Conversation mode on" : "Conversation mode off");
  } catch (e) { toast("⚠ " + e.message); }
});
$("#wakeToggle").addEventListener("click", async () => {
  try {
    const r = await api("POST", "/api/conversation", { on: !wakeOn, wake: true });
    applyConvStatus(r);
    toast(wakeOn ? "Wake word on — say “Hello Abhishek”" : "Wake word off");
  } catch (e) { toast("⚠ " + e.message); }
});
$("#faceToggle").addEventListener("click", async () => {
  try {
    const r = await api("POST", "/api/face_track", { on: !faceOn });
    faceOn = !!r.face_track_on; updateFaceUI();
    toast(faceOn ? "Face tracking on" : "Face tracking off");
  } catch (e) { toast("⚠ " + e.message); }
});

// ---- robot speaks (type → TTS) --------------------------------------------
async function robotSay(text) {
  text = (text || "").trim();
  if (!text) return toast("Type something to say");
  const btn = $("#sayBtn");
  btn.disabled = true;
  try {
    await api("POST", "/api/say", { text });
    addVoiceEntry("said", text);
    $("#sayText").value = "";
  } catch (e) {
    toast("⚠ " + e.message);
  } finally {
    btn.disabled = false;
  }
}
$("#sayBtn").addEventListener("click", () => robotSay($("#sayText").value));
$("#sayText").addEventListener("keydown", (e) => { if (e.key === "Enter") robotSay($("#sayText").value); });

// ---- voice control (USB mic → AssemblyAI → Haiku) -------------------------
let listening = false;
async function voiceListen() {
  if (listening) return;
  listening = true;
  const btn = $("#voiceBtn");
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "● Listening…";
  btn.classList.add("rec");
  try {
    // generate=true builds the sequence; run=false so the user can review first
    const res = await api("POST", "/api/voice", { generate: true, run: false });
    if (res.transcript) { $("#aiPrompt").value = res.transcript; addVoiceEntry("heard", res.transcript); }
    if (res.spoken) addVoiceEntry("said", res.spoken);
    if (res.error) {
      toast("⚠ " + (res.transcript ? `${res.transcript} — ${res.error}` : res.error));
    } else if (res.sequence) {
      const seq = res.sequence;
      draftSteps = JSON.parse(JSON.stringify(seq.steps));
      $("#seqName").value = seq.name || "ai-sequence";
      $("#loopChk").checked = !!seq.loop;
      renderSteps();
      toast(`Heard “${res.transcript}” → built “${seq.name}” (${seq.steps.length} steps)`);
    } else if (res.transcript) {
      toast(`Heard “${res.transcript}”`);
    }
  } catch (e) {
    toast("⚠ " + e.message);
  } finally {
    listening = false;
    btn.disabled = false;
    btn.textContent = label;
    btn.classList.remove("rec");
  }
}
$("#voiceBtn").addEventListener("click", voiceListen);

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

// ---- feature visibility ---------------------------------------------------
// Single source of truth for which panels are shown. Called once at startup and
// again whenever a capability changes (e.g. a camera ribbon cable reseated), so
// the two paths can never disagree about a panel.
function applyFeatureVisibility(s) {
  const show = (sel, on) => $(sel).classList.toggle("hidden", !on);
  show("#aiCard", s.llm);
  show("#voiceBtn", s.voice);
  show("#camCard", s.camera);
  // Voice log box: shown if we can hear, speak, converse or track.
  show("#voiceLogCard", s.voice || s.tts || s.conversation_avail || s.face_track);
  show("#sayRow", s.tts);
  show("#convToggle", s.conversation_avail);
  show("#wakeToggle", s.conversation_avail);
  show("#faceToggle", s.face_track);
  show("#langSelect", s.speech_switchable);
  show("#partyBtn", s.music_avail);
}

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
    // conversation + face-tracking live status
    if (s.conversation) {
      applyConvStatus(s.conversation);
      if (s.conversation.n > lastConvN) {
        lastConvN = s.conversation.n;
        addVoiceEntry("heard", s.conversation.user);
        addVoiceEntry("said", s.conversation.reply);
      }
    }
    faceOn = !!s.face_track_on; updateFaceUI();
    paintRobot(s.angles);
    if (robot) {
      robot.setFaceSeen(!!s.face_seen);
      robot.setSpeaking(!!(s.conversation && s.conversation.on
                           && s.conversation.status === "speaking"));
    }
    updateConvStatus(s);
    if (s.speech_switchable) setupLang(s.speech);
    partyOn = !!s.party; updatePartyUI();
    // Capabilities can come and go (e.g. camera ribbon cable reseated) —
    // reflect them live through the same path init() uses.
    const camChanged = s.camera !== STATE.camera;
    Object.assign(STATE, {
      camera: s.camera, llm: s.llm, voice: s.voice, tts: s.tts,
      face_track: s.face_track, conversation_avail: s.conversation_avail,
      speech_switchable: s.speech_switchable, music_avail: s.music_avail,
    });
    applyFeatureVisibility(STATE);
    if (camChanged) setCamera(s.camera);
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
  robot = new ArtyomRobot("#robot", {
    head: STATE.channels[0],
    arms: (STATE.arms && STATE.arms.length) ? STATE.arms : [[1, 2], [3, 4]],
    invert: STATE.invert || [],
    neutral: STATE.start_angle,
  });
  paintRobot(STATE.angles);
  renderServos();
  renderSteps();
  await loadSequences();
  applyFeatureVisibility(STATE);
  if (STATE.speech_switchable) setupLang(STATE.speech);
  partyOn = !!STATE.party; updatePartyUI();
  applyConvStatus(STATE.conversation);
  faceOn = !!STATE.face_track_on;
  lastConvN = (STATE.conversation && STATE.conversation.n) || 0;
  updateFaceUI();
  setCamera(STATE.camera);   // auto-start feed when a camera is present
  setInterval(poll, 300);
})();
