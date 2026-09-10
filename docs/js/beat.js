"use strict";
/**
 * The party beat, rebuilt in WebAudio.
 *
 * A port of music.generate_party_loop: the same 128 BPM pattern the Raspberry
 * Pi synthesizes with numpy when there are no audio files to play — kick,
 * snare, hats, a square bassline and a sine arpeggio. Rendered once into an
 * AudioBuffer, then looped.
 *
 * No files, no CDN, nothing to download: the demo makes its own music, exactly
 * like the robot does.
 */
(function (global) {
  const NOTE = {
    A2: 110.0, F2: 87.31, G2: 98.0,
    A3: 220.0, C4: 261.63, E4: 329.63, A4: 440.0,
  };
  const BASS = ["A2", "A2", "F2", "G2"];   // one root per bar
  const ARP = ["A3", "C4", "E4", "A4"];

  function render(ctx, { bpm = 128, bars = 4 } = {}) {
    const rate = ctx.sampleRate;
    const spb = 60 / bpm;                  // seconds per beat
    const beats = bars * 4;
    const n = Math.floor(spb * beats * rate);
    const buf = ctx.createBuffer(1, n, rate);
    const out = buf.getChannelData(0);

    const at = (t) => Math.floor(t * rate);

    function kick(start) {
      const s = at(start);
      const len = Math.min(Math.floor(0.18 * rate), n - s);
      if (s >= n || len <= 0) return;
      let phase = 0;
      for (let i = 0; i < len; i++) {
        const t = i / rate;
        const freq = 110 * Math.exp(-t / 0.03) + 45;   // punchy pitch drop
        phase += (2 * Math.PI * freq) / rate;
        out[s + i] += 0.95 * Math.sin(phase) * Math.exp(-t / 0.09);
      }
    }

    function snare(start) {
      const s = at(start);
      const len = Math.min(Math.floor(0.18 * rate), n - s);
      if (s >= n || len <= 0) return;
      for (let i = 0; i < len; i++) {
        const t = i / rate;
        const noise = (Math.random() * 2 - 1) * Math.exp(-t / 0.06);
        const tone = Math.sin(2 * Math.PI * 180 * t) * Math.exp(-t / 0.05);
        out[s + i] += 0.4 * noise + 0.2 * tone;
      }
    }

    function hat(start, dur = 0.04) {
      const s = at(start);
      const len = Math.min(Math.floor(dur * rate), n - s);
      if (s >= n || len <= 0) return;
      for (let i = 0; i < len; i++) {
        const t = i / rate;
        out[s + i] += 0.13 * (Math.random() * 2 - 1) * Math.exp(-t / 0.012);
      }
    }

    function tone(start, dur, freq, amp = 0.2, kind = "square") {
      const s = at(start);
      const len = Math.min(Math.floor(dur * rate), n - s);
      if (s >= n || len <= 0) return;
      for (let i = 0; i < len; i++) {
        const t = i / rate;
        const sine = Math.sin(2 * Math.PI * freq * t);
        const w = kind === "square" ? Math.sign(sine) : sine;
        out[s + i] += amp * w * Math.exp(-t / (dur * 0.6));
      }
    }

    for (let b = 0; b < beats; b++) {
      const bt = b * spb;
      kick(bt);                                  // four-on-the-floor
      hat(bt + spb * 0.25);
      hat(bt + spb * 0.5);
      hat(bt + spb * 0.75);
      if (b % 4 === 1 || b % 4 === 3) snare(bt); // snare on 2 & 4
    }
    for (let bar = 0; bar < bars; bar++) {       // bassline, eighth notes
      const root = NOTE[BASS[bar % BASS.length]];
      for (let e = 0; e < 8; e++) {
        tone(bar * 4 * spb + e * (spb / 2), (spb / 2) * 0.9, root, 0.18, "square");
      }
    }
    for (let i = 0; i < beats * 2; i++) {        // arpeggio sparkle
      tone(i * (spb / 2), (spb / 2) * 0.8, NOTE[ARP[i % ARP.length]], 0.11, "sine");
    }

    let peak = 0;
    for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(out[i]));
    const norm = (peak || 1) / 0.9;
    for (let i = 0; i < n; i++) out[i] /= norm;

    return buf;
  }

  class Beat {
    constructor() {
      this.ctx = null;
      this.buffer = null;
      this.source = null;
      this.gain = null;
    }

    get playing() {
      return this.source !== null;
    }

    /** Browsers require a user gesture before audio; call this from a click. */
    start() {
      if (this.playing) return;
      if (!this.ctx) {
        const AC = global.AudioContext || global.webkitAudioContext;
        if (!AC) return;
        this.ctx = new AC();
      }
      if (this.ctx.state === "suspended") this.ctx.resume();
      if (!this.buffer) this.buffer = render(this.ctx);

      this.gain = this.ctx.createGain();
      this.gain.gain.value = 0.55;
      this.gain.connect(this.ctx.destination);

      this.source = this.ctx.createBufferSource();
      this.source.buffer = this.buffer;
      this.source.loop = true;
      this.source.connect(this.gain);
      this.source.start();
    }

    stop() {
      if (!this.source) return;
      try { this.source.stop(); } catch (_) { /* already stopped */ }
      this.source.disconnect();
      this.source = null;
    }
  }

  global.Beat = Beat;
})(window);
