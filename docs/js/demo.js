"use strict";
/**
 * Browser demo wiring.
 *
 * Loads the robot's real config slice and sequence file, then drives the shared
 * SVG robot through the ported motion engine. Everything here is client-side —
 * the page is served as static files from GitHub Pages.
 */
const $ = (s) => document.querySelector(s);

let CFG = null;
let SEQS = {};
let ctrl = null;
let runner = null;
let robot = null;
let beat = null;
let stopTalking = null;
let tracking = null;

/* --------------------------------------------------------------------- ui */
let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2200);
}

function setNowPlaying(text) {
  $("#nowPlaying").textContent = text || "idle";
  $("#nowPlaying").classList.toggle("live", !!text && text !== "idle");
}

/* ---------------------------------------------------------------- servos */
function renderServos() {
  const wrap = $("#servos");
  wrap.innerHTML = "";
  const parked = new Set((CFG.park_channels || []).map(Number));

  CFG.channels.forEach((ch) => {
    const name = CFG.names[String(ch)] || `Servo ${ch}`;
    const row = document.createElement("div");
    row.className = "servo";
    row.innerHTML = `
      <div class="name"><span class="nm"></span> <span class="ch">ch ${ch}</span></div>
      <input type="range" min="${CFG.min_angle}" max="${CFG.max_angle}"
             value="${ctrl.angles[ch]}" data-ch="${ch}" />
      <output class="val">${Math.round(ctrl.angles[ch])}°</output>`;
    row.querySelector(".nm").textContent = name;
    if (parked.has(ch)) row.classList.add("parked");
    wrap.appendChild(row);

    const range = row.querySelector("input");
    range.addEventListener("input", () => {
      runner.stop();
      ctrl.setAngle(ch, range.value);
    });
  });

  if (parked.size) {
    const names = [...parked].map((c) => CFG.names[String(c)] || `ch${c}`).join(", ");
    $("#servoNote").innerHTML =
      `<b>${names}</b> is a continuous-rotation servo — it can't hold an angle `
      + `(90° means "stopped"), so the code pins it to neutral. Its slider is `
      + `deliberately inert, exactly like the real robot.`;
  }
}

/** Repaint sliders when a sequence is what's moving the servos. */
function syncSliders(angles) {
  for (const [ch, v] of Object.entries(angles)) {
    const range = document.querySelector(`input[data-ch="${ch}"]`);
    if (!range) continue;
    range.value = v;
    const out = range.parentElement.querySelector(".val");
    if (out) out.textContent = `${Math.round(v)}°`;
  }
}

/* ------------------------------------------------------------- sequences */
function renderSequences() {
  const box = $("#seqList");
  box.innerHTML = "";
  const names = Object.keys(SEQS).sort();
  $("#seqCount").textContent = `${names.length} built in`;

  names.forEach((name) => {
    const s = SEQS[name];
    const btn = document.createElement("button");
    btn.className = "seq";
    btn.innerHTML = `<span class="seq-name"></span>
      <span class="seq-meta">${s.steps.length} steps${s.loop ? " · loops" : ""}</span>`;
    btn.querySelector(".seq-name").textContent = name;
    btn.addEventListener("click", () => play(name));
    box.appendChild(btn);
  });
}

function play(name) {
  const seq = SEQS[name];
  if (!seq) return;
  stopEverything({ keepBeat: beat && beat.playing });
  runner.start(Object.assign({ name }, seq));
  setNowPlaying(`▶ ${name}${seq.loop ? " (looping)" : ""}`);
  document.querySelectorAll(".seq").forEach((b) => {
    b.classList.toggle("on", b.querySelector(".seq-name").textContent === name);
  });
}

/* ------------------------------------------------------------------ talk */
function speak(text) {
  if (!text.trim()) return toast("Type something first");
  stopEverything();

  const arms = (CFG.arms || []).map((a) => a.map(Number));
  const chans = arms.flat();
  if (!chans.length) return toast("No arm channels configured");

  const wantVoice = $("#voiceChk").checked && "speechSynthesis" in window;
  // Gesture length has to match the audio, so measure the utterance when we can
  // and fall back to a reading-speed estimate when the browser has no voices.
  const estimate = Math.max(1.2, (text.split(/\s+/).length / 2.6));

  const run = (duration) => {
    const keys = Engine.talkKeyframes(text, duration, arms);
    robot.setSpeaking(true);
    setNowPlaying("🔊 speaking");
    stopTalking = Engine.animateKeyframes(ctrl, keys, chans, () => {
      robot.setSpeaking(false);
      setNowPlaying(null);
      stopTalking = null;
    });
  };

  if (!wantVoice) return run(estimate);

  const utter = new SpeechSynthesisUtterance(text);
  utter.rate = 1.0;
  const started = performance.now();
  utter.onend = () => {
    const actual = (performance.now() - started) / 1000;
    if (stopTalking && Math.abs(actual - estimate) > 1.5) {
      // The voice finished well off the estimate — settle the arms rather than
      // gesturing into silence.
      stopTalking();
      robot.setSpeaking(false);
      setNowPlaying(null);
      stopTalking = null;
    }
  };
  speechSynthesis.cancel();
  speechSynthesis.speak(utter);
  run(estimate);
}

/* ----------------------------------------------------------------- party */
function toggleParty() {
  if (beat && beat.playing) {
    stopEverything();
    $("#partyBtn").classList.remove("on");
    return;
  }
  stopEverything();
  beat = beat || new Beat();
  beat.start();
  const dances = (CFG.party_dances || []).filter((n) => n in SEQS);
  const pool = dances.length ? dances : Object.keys(SEQS).filter((n) => /^dance/i.test(n));
  if (!pool.length) return toast("No dance sequences found");
  const pick = pool[Math.floor(Math.random() * pool.length)];
  runner.start(Object.assign({ name: pick }, SEQS[pick]));
  setNowPlaying(`🎉 ${pick}`);
  $("#partyBtn").classList.add("on");
}

/* ------------------------------------------------------------- face track */
async function toggleTracking() {
  if (tracking) {
    tracking.stop();
    tracking = null;
    $("#trackBtn").classList.remove("on");
    robot.setFaceSeen(false);
    setNowPlaying(null);
    return;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return toast("This browser has no camera API");
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { width: 320, height: 240 } });
  } catch (e) {
    return toast("Camera permission denied");
  }

  const video = $("#camFeed");
  video.srcObject = stream;
  await video.play();

  const canvas = document.createElement("canvas");
  canvas.width = 96;
  canvas.height = 72;
  const g = canvas.getContext("2d", { willReadFrequently: true });

  const detector = ("FaceDetector" in window) ? new window.FaceDetector({ fastMode: true }) : null;
  let prev = null;
  const head = Number(CFG.face_track_channel ?? 0);
  const gain = 25;        // matches face_track_gain
  const deadzone = 0.04;  // matches face_track_deadzone

  const tick = async () => {
    let cx = null;
    if (detector) {
      try {
        const faces = await detector.detect(video);
        if (faces.length) {
          const f = faces.sort((a, b) =>
            b.boundingBox.width * b.boundingBox.height
            - a.boundingBox.width * a.boundingBox.height)[0];
          cx = (f.boundingBox.x + f.boundingBox.width / 2) / video.videoWidth;
        }
      } catch (_) { /* fall through to motion tracking */ }
    }
    if (cx === null) {
      // No FaceDetector (most browsers): track the centroid of frame-to-frame
      // motion instead. Less precise than the Pi's Haar cascade, same control loop.
      g.drawImage(video, 0, 0, canvas.width, canvas.height);
      const frame = g.getImageData(0, 0, canvas.width, canvas.height).data;
      if (prev) {
        let sum = 0, weight = 0;
        for (let y = 0; y < canvas.height; y += 2) {
          for (let x = 0; x < canvas.width; x += 2) {
            const i = (y * canvas.width + x) * 4;
            const d = Math.abs(frame[i] - prev[i]) + Math.abs(frame[i + 1] - prev[i + 1]);
            if (d > 40) { sum += x * d; weight += d; }
          }
        }
        if (weight > 3000) cx = (sum / weight) / canvas.width;
      }
      prev = frame;
    }

    robot.setFaceSeen(cx !== null);
    if (cx !== null) {
      // Mirror: the camera shows you flipped, so invert to follow, not mirror.
      const err = (1 - cx) - 0.5;
      if (Math.abs(err) > deadzone) {
        ctrl.setAngle(head, ctrl.angles[head] + gain * err * 2);
        syncSliders({ [head]: ctrl.angles[head] });
      }
    }
  };

  const timer = setInterval(tick, 100);   // 10 Hz, same as vision.py
  tracking = {
    stop() {
      clearInterval(timer);
      stream.getTracks().forEach((t) => t.stop());
      video.srcObject = null;
    },
  };
  $("#trackBtn").classList.add("on");
  setNowPlaying("🙂 tracking");
  toast(detector ? "Face tracking on" : "Tracking motion (no FaceDetector in this browser)");
}

/* ------------------------------------------------------------------ stop */
function stopEverything({ keepBeat = false } = {}) {
  runner.stop();
  if (stopTalking) { stopTalking(); stopTalking = null; }
  if (window.speechSynthesis) speechSynthesis.cancel();
  robot.setSpeaking(false);
  if (!keepBeat && beat) { beat.stop(); $("#partyBtn").classList.remove("on"); }
  document.querySelectorAll(".seq.on").forEach((b) => b.classList.remove("on"));
  setNowPlaying(null);
}

/* ------------------------------------------------------------------ init */
async function loadJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

(async function init() {
  try {
    [CFG, SEQS] = await Promise.all([
      loadJSON("data/config.json"),
      loadJSON("data/default_sequences.json"),
    ]);
  } catch (e) {
    $("#statusBadge").textContent = "failed to load";
    toast("Could not load robot data — serve this folder over http, not file://");
    return;
  }

  ctrl = new Engine.Controller(CFG, (angles) => {
    robot.setAngles(angles);
    syncSliders(angles);
  });
  runner = new Engine.Runner(ctrl);
  runner.onStateChange = (name) => { if (!name) setNowPlaying(null); };

  robot = new ArtyomRobot("#robot", {
    head: Number(CFG.face_track_channel ?? 0),
    arms: (CFG.arms || []).map((a) => a.map(Number)),
    invert: (CFG.invert || []).map(Number),
    neutral: CFG.start_angle,
  });
  robot.setAngles(ctrl.angles);

  $("#mockBadge").textContent = `${CFG.channels.length} channels`;
  renderServos();
  renderSequences();

  $("#centerBtn").addEventListener("click", () => { stopEverything(); ctrl.center(); });
  $("#stopBtn").addEventListener("click", () => stopEverything());
  $("#sayBtn").addEventListener("click", () => speak($("#sayText").value));
  $("#sayText").addEventListener("keydown", (e) => {
    if (e.key === "Enter") speak($("#sayText").value);
  });
  $("#partyBtn").addEventListener("click", toggleParty);
  $("#trackBtn").addEventListener("click", toggleTracking);

  // A friendly opening move so the page isn't static on arrival.
  setTimeout(() => play("wave-right"), 600);
})();
