"use strict";
/**
 * The robot's motion core, ported to the browser.
 *
 * This is a faithful port of the two Python pieces that matter, so the demo is
 * running the robot's real logic rather than a canned animation:
 *
 *   Runner._ease      (servo.py)        — 20 ms linear interpolation between poses
 *   talk_keyframes    (conversation.py) — arm gestures derived from reply text
 *
 * Keeping them in step matters: tests/test_gestures.py pins the Python side,
 * and docs/js/engine.test.html re-runs the equivalent assertions here.
 */

const TICK = 20;          // ms, matches Runner.TICK = 0.02
const REST = 90;          // matches conversation.REST

/* ------------------------------------------------------------------ servos */
class Controller {
  /** Mirrors servo.Controller: limits, mirror-mounting and parked channels. */
  constructor(cfg, onChange) {
    this.cfg = cfg;
    this.onChange = onChange;
    this.minAngle = cfg.min_angle;
    this.maxAngle = cfg.max_angle;
    this.start = cfg.start_angle;
    this.channels = cfg.channels.map(Number);
    this.park = new Set((cfg.park_channels || []).map(Number));
    this.limits = {};
    for (const c of this.channels) {
      const lim = (cfg.limits || {})[String(c)] || [this.minAngle, this.maxAngle];
      this.limits[c] = [Math.max(this.minAngle, lim[0]), Math.min(this.maxAngle, lim[1])];
    }
    this.angles = {};
    for (const c of this.channels) this.angles[c] = this.start;
  }

  clamp(a) {
    return Math.max(this.minAngle, Math.min(this.maxAngle, Number(a)));
  }

  setAngle(channel, angle) {
    const ch = Number(channel);
    if (!(ch in this.angles)) return null;
    // A continuous-rotation servo cannot hold an angle — 90 is "stopped".
    if (this.park.has(ch)) angle = this.start;
    const [lo, hi] = this.limits[ch];
    const v = Math.max(lo, Math.min(hi, Number(angle)));
    this.angles[ch] = v;
    if (this.onChange) this.onChange(this.angles);
    return v;
  }

  center() {
    for (const c of this.channels) this.setAngle(c, this.start);
  }
}

/* ---------------------------------------------------------------- sequences */
class Runner {
  /** Plays one sequence at a time, easing between poses. */
  constructor(ctrl) {
    this.ctrl = ctrl;
    this.timer = null;
    this.current = null;
    this.onStateChange = null;
  }

  get running() {
    return this.timer !== null;
  }

  start(seq) {
    this.stop();
    this.current = seq.name || "?";
    this._plan = this._compile(seq);
    this._i = 0;
    this._loop = !!seq.loop;
    this._emit();
    this._next();
  }

  stop() {
    if (this.timer) { clearTimeout(this.timer); clearInterval(this.timer); }
    this.timer = null;
    this.current = null;
    this._emit();
  }

  _emit() {
    if (this.onStateChange) this.onStateChange(this.current);
  }

  /** Drop channels this robot doesn't have — legacy sequences may name them. */
  _compile(seq) {
    return (seq.steps || []).map((st) => {
      const targets = {};
      for (const [c, a] of Object.entries(st.angles || {})) {
        const ch = Number(c);
        if (ch in this.ctrl.angles) targets[ch] = this.ctrl.clamp(a);
      }
      return {
        targets,
        moveTime: Number(st.move_time ?? 0.4) * 1000,
        hold: Number(st.hold ?? 0) * 1000,
      };
    });
  }

  _next() {
    if (!this._plan || !this._plan.length) return this.stop();
    if (this._i >= this._plan.length) {
      if (!this._loop) return this.stop();
      this._i = 0;
    }
    const step = this._plan[this._i++];
    if (!Object.keys(step.targets).length) return this._afterHold(step);
    this._ease(step, () => this._afterHold(step));
  }

  _afterHold(step) {
    this.timer = setTimeout(() => this._next(), step.hold);
  }

  // Port of Runner._ease: N ticks of linear interpolation, landing exactly on
  // the target so a sequence never drifts.
  _ease(step, done) {
    const from = {};
    for (const ch of Object.keys(step.targets)) from[ch] = this.ctrl.angles[ch];
    const steps = Math.max(1, Math.round(step.moveTime / TICK));
    let i = 0;
    this.timer = setInterval(() => {
      i += 1;
      const f = i / steps;
      for (const [ch, target] of Object.entries(step.targets)) {
        this.ctrl.setAngle(ch, from[ch] + (target - from[ch]) * f);
      }
      if (i >= steps) {
        clearInterval(this.timer);
        this.timer = null;
        done();
      }
    }, TICK);
  }
}

/* ----------------------------------------------------------------- gestures */
/**
 * Port of conversation.talk_keyframes.
 *
 * Turns a spoken sentence into an arm-motion timeline. Movement comes from the
 * *content* of the sentence, not from randomness:
 *   - one beat per word, spread across the audio so gestures track the rhythm
 *   - amplitude grows with word length (longer/emphasised words gesture bigger)
 *   - '?' and '!' throw every arm; commas briefly settle; sentence ends relax
 *     to rest and swap which arm leads
 *
 * Returns [[tSeconds, {channel: angle}], ...] spanning [0, duration].
 */
function talkKeyframes(text, duration, arms, rest = REST, lo = 55, hi = 140) {
  const chans = arms.flat();
  const restPose = Object.fromEntries(chans.map((c) => [c, rest]));
  const tokens = (text || "").match(/\S+/g) || [];
  if (!tokens.length || duration <= 0 || !arms.length) {
    return [[0, { ...restPose }], [Math.max(duration, 0.1), { ...restPose }]];
  }

  const clamp = (a) => Math.max(lo, Math.min(hi, a));
  const armPose = (pose, arm, amp) => {
    pose[arm[0]] = clamp(rest + amp);                    // shoulder raises
    if (arm.length > 1) pose[arm[1]] = clamp(rest + amp * 0.9);  // elbow follows
  };

  const per = duration / tokens.length;
  const keys = [[0, { ...restPose }]];
  let lead = 0;

  tokens.forEach((tok, i) => {
    const last = tok[tok.length - 1];
    const core = tok.replace(/[^0-9A-Za-z']/g, "");
    const n = core.length || tok.length;
    const strong = last === "!" || last === "?";
    const sentenceEnd = strong || last === ".";
    const clause = [",", ";", ":"].includes(last);

    let amp = 14 + 3 * Math.min(8, n);      // 17..38 deg, grows with word length
    if (strong) amp += 8;                   // questions/exclamations punch harder

    const pose = { ...restPose };
    arms.forEach((arm, k) => {
      if (k === lead % arms.length || strong) armPose(pose, arm, amp);
      else armPose(pose, arm, amp * 0.45);  // followers stay lively, not equal
    });
    keys.push([(i + 0.5) * per, pose]);     // beat peaks mid-word

    if (sentenceEnd) {
      keys.push([(i + 1) * per, { ...restPose }]);
    } else if (clause) {
      const settle = Object.fromEntries(chans.map((c) => [c, clamp(rest + 0.2 * amp)]));
      keys.push([(i + 0.95) * per, settle]);
    }
    lead += 1;
  });

  keys.push([duration, { ...restPose }]);
  keys.sort((a, b) => a[0] - b[0]);
  return keys;
}

/** Drive `ctrl` along a keyframe timeline. Returns a stop() handle. */
function animateKeyframes(ctrl, keys, chans, onDone) {
  const t0 = performance.now();
  const endT = keys[keys.length - 1][0] * 1000;
  const timer = setInterval(() => {
    const now = performance.now() - t0;
    if (now >= endT) {
      clearInterval(timer);
      for (const c of chans) ctrl.setAngle(c, REST);
      if (onDone) onDone();
      return;
    }
    let pose = keys[keys.length - 1][1];
    for (let j = 1; j < keys.length; j++) {
      if (keys[j][0] * 1000 >= now) {
        const [t0s, p0] = keys[j - 1];
        const [t1s, p1] = keys[j];
        const span = (t1s - t0s) * 1000;
        const f = span <= 0 ? 0 : (now - t0s * 1000) / span;
        pose = {};
        for (const c of chans) {
          const a = p0[c] ?? REST;
          const b = p1[c] ?? REST;
          pose[c] = a + (b - a) * f;
        }
        break;
      }
    }
    for (const c of chans) ctrl.setAngle(c, pose[c] ?? REST);
  }, TICK);

  return () => {
    clearInterval(timer);
    for (const c of chans) ctrl.setAngle(c, REST);
  };
}

window.Engine = { Controller, Runner, talkKeyframes, animateKeyframes, REST, TICK };
