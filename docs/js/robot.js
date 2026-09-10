"use strict";
/**
 * ArtyomRobot — an SVG stand-in for the physical robot.
 *
 * Draws the cardboard build the code actually drives: a spiky-hair head on a
 * pan servo, a box torso in a shirt and tie, and two arms of upper-arm +
 * forearm segments. Feed it the same `{channel: angle}` map the Flask API
 * reports and it mirrors the hardware.
 *
 * Used twice: inside the live web UI (so mock mode on a laptop is visual, and
 * on the Pi you can watch the robot without looking at the robot), and in the
 * browser demo, where it is the only robot there is.
 *
 * Angle convention matches the hardware: 90 is neutral. A shoulder at 90 hangs
 * straight down against the side of the torso; raising the angle lifts the arm
 * FORWARD and up. The arms have no sideways travel and cannot go behind the
 * body - see elevation() and limb() for how that is drawn in a view
 * that has no depth to work with.
 */
(function (global) {
  const SVG = "http://www.w3.org/2000/svg";
  const el = (name, attrs = {}) => {
    const node = document.createElementNS(SVG, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };

  const DEFAULTS = {
    head: 0,                      // pan servo
    arms: [[1, 2], [3, 4]],       // [shoulder, elbow] per arm; elbow optional
    invert: [3, 4],
    neutral: 90,
    headSweep: 22,                // px the head slides at full pan
    showAngles: true,
  };

  class ArtyomRobot {
    constructor(mount, opts = {}) {
      this.mount = typeof mount === "string" ? document.querySelector(mount) : mount;
      this.opts = Object.assign({}, DEFAULTS, opts);
      this.invert = new Set(this.opts.invert || []);
      this.angles = {};
      this.parts = {};
      this._build();
      this.setAngles({});
    }

    // -- geometry -----------------------------------------------------------
    // One arm = a shoulder pivot, an upper segment, an elbow pivot, a forearm.
    // Left/right differ only in which side of the torso they hang from and
    // which way a raise rotates.
    // `dir` tips each arm away from the body's centre line as it rises, so the
    // two arms fan apart slightly instead of overlapping the tie. Mirror-mounted
    // channels need no special case here: both arms raise forward together,
    // which is exactly what `invert` achieves on the hardware.
    static get LAYOUT() {
      return {
        neck: { x: 200, y: 168 },
        shoulders: [
          { x: 132, y: 208, dir: +1 },   // robot's right (viewer's left)
          { x: 268, y: 208, dir: -1 },   // robot's left
        ],
        upper: 62,
        fore: 54,
        hand: 14,
      };
    }

    // -- projecting a forward raise onto a front view -----------------------
    // The real arms are flat panels hinged at the top corners of the torso: they
    // lift forward and up, then come back down. There is no sideways travel and
    // nothing goes behind the body.
    //
    // A dead-on front view can't show motion coming toward the viewer, so the
    // raise is projected: a little of it becomes rotation in the picture plane
    // (the hand arcs up and slightly out) and the rest becomes foreshortening
    // (the limb shortens as it points at you). Rotating by the full angle
    // instead would draw a sideways arm the hardware cannot produce.

    // How far off the front axis the arms are drawn from. The torso and face are
    // dead-on, but the limbs get a slight three-quarter camera - just enough for
    // a raise coming toward the viewer to be legible as motion.
    static get VIEW_SKEW() { return Math.sin((28 * Math.PI) / 180); }

    // Ceiling on a single joint's contribution. Shoulder and elbow compound, so
    // an "arm up" pose (dance-disco holds ch1 and ch2 both at 160) still clears
    // horizontal and puts the hand above the shoulder, which is the whole point
    // of that pose - while neither joint alone can fling a limb past vertical.
    static get MAX_LIFT() { return 80; }

    /** Servo angle -> degrees of forward elevation. 90 = hanging straight down. */
    static elevation(angle, neutral) {
      const d = angle - neutral;
      // Below neutral the arm is already resting against the body, so travel
      // there barely shows - but every dance alternates 45 with ~160, and
      // flattening the low half would cost half the visible swing. A shallow
      // slope keeps those poses distinct without ever reading as a backswing.
      return d >= 0
        ? Math.min(ArtyomRobot.MAX_LIFT, d * 0.85)
        : Math.max(-14, d * 0.28);
    }

    /** Elevation -> an SVG transform for one limb segment.
     *
     * Projects the arm's real direction through the three-quarter camera. The
     * limb points straight down the -y axis, so in the robot's own frame a raise
     * of `deg` splits into a downward component cos(deg) and a forward component
     * sin(deg); the camera turns that forward component into a small sideways
     * offset. What survives is the on-screen angle and how much of the limb's
     * length is still facing us.
     */
    static limb(deg, dir) {
      const rad = (deg * Math.PI) / 180;
      const down = Math.cos(rad);
      const out = Math.sin(rad) * ArtyomRobot.VIEW_SKEW;
      const swing = ((Math.atan2(out, down) * 180) / Math.PI) * dir;
      const foreshorten = Math.hypot(down, out);
      return {
        swing,
        foreshorten,
        transform: `rotate(${swing.toFixed(2)}) scale(1 ${foreshorten.toFixed(3)})`,
      };
    }

    _build() {
      const svg = el("svg", {
        viewBox: "0 50 400 342",
        class: "robot-svg",
        role: "img",
        "aria-label": "Artyom robot",
      });

      const defs = el("defs");
      defs.innerHTML = `
        <linearGradient id="card" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"  stop-color="#c8a06a"/>
          <stop offset="100%" stop-color="#a9814c"/>
        </linearGradient>
        <linearGradient id="armbox" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%"  stop-color="#4aa3e0"/>
          <stop offset="100%" stop-color="#2a7cb8"/>
        </linearGradient>
        <filter id="soft" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="6" stdDeviation="7" flood-opacity="0.35"/>
        </filter>`;
      svg.appendChild(defs);

      const L = ArtyomRobot.LAYOUT;
      const scene = el("g", { filter: "url(#soft)" });

      // --- torso: a cardboard box, dressed in a shirt and tie ---------------
      const torso = el("g");
      torso.appendChild(el("path", {
        d: "M142 190 L258 190 L262 378 L138 378 Z",
        fill: "url(#card)", stroke: "#7d5c33", "stroke-width": "2",
      }));
      torso.appendChild(el("path", {                       // shirt
        d: "M151 200 L249 200 L253 368 L147 368 Z",
        fill: "#f6f4ef", stroke: "#cfc9bd", "stroke-width": "1.5",
      }));
      torso.appendChild(el("path", {                       // collar
        d: "M178 200 L200 232 L222 200 L213 198 L200 216 L187 198 Z",
        fill: "#e9e5dc", stroke: "#c3bcae", "stroke-width": "1",
      }));
      torso.appendChild(el("path", {                       // tie
        d: "M200 232 L211 246 L207 322 L200 336 L193 322 L189 246 Z",
        fill: "#d9483f", stroke: "#a5342d", "stroke-width": "1.5",
      }));
      torso.appendChild(el("rect", {                       // pocket
        x: "222", y: "252", width: "22", height: "26", rx: "2",
        fill: "none", stroke: "#c3bcae", "stroke-width": "1.5",
      }));
      torso.appendChild(el("rect", {                       // belt
        x: "147", y: "350", width: "106", height: "15",
        fill: "#2b2b2b", opacity: "0.85",
      }));
      // --- arms -------------------------------------------------------------
      // Drawn in front of the torso: a forward raise passes over the chest.
      // The shoulder pivots sit just outside the torso silhouette, so at rest
      // the arms still hang flush against the sides.
      scene.appendChild(torso);
      this.parts.arms = (this.opts.arms || []).map((pair, i) => {
        const side = L.shoulders[i] || L.shoulders[L.shoulders.length - 1];
        const shoulderG = el("g");
        const elbowG = el("g");

        const seg = (len, w) => el("rect", {
          x: -w / 2, y: 0, width: w, height: len, rx: 5,
          fill: "url(#armbox)", stroke: "#1d5f8f", "stroke-width": "2",
        });
        const joint = el("circle", { r: 7, fill: "#2b3440", stroke: "#6f7d8c", "stroke-width": "2" });

        shoulderG.appendChild(seg(L.upper, 24));
        elbowG.appendChild(seg(L.fore, 20));
        elbowG.appendChild(el("rect", {                     // hand
          x: -11, y: L.fore, width: 22, height: L.hand, rx: 4,
          fill: "#f0e6d2", stroke: "#c2b295", "stroke-width": "2",
        }));

        // The forearm is a *sibling* of the upper arm, not a child. Nesting it
        // would rotate it inside the upper arm's foreshortening scale, and
        // rotating within a non-uniformly scaled frame skews the angle - the
        // forearm ended up folding down when it should have carried on lifting.
        // Instead setAngles() walks the upper arm to find where the elbow landed
        // and places the forearm there itself.
        const root = el("g", { transform: `translate(${side.x} ${side.y})` });
        root.appendChild(shoulderG);
        root.appendChild(elbowG);
        root.appendChild(joint);
        scene.appendChild(root);

        return { shoulderG, elbowG, side, shoulder: pair[0], elbow: pair[1] };
      });

      // --- head (drawn last so it sits above the shoulders) -----------------
      const headG = el("g");
      headG.appendChild(el("rect", {                        // neck
        x: "188", y: "158", width: "24", height: "36", fill: "#8a6a3f",
      }));
      const hair = el("path", {                             // the spiky cutout
        d: "M158 110 L166 74 L176 104 L184 64 L194 100 L200 60 L208 100 "
         + "L216 64 L226 104 L234 74 L242 110 Z",
        fill: "#4a5058", stroke: "#343941", "stroke-width": "2",
      });
      headG.appendChild(hair);
      headG.appendChild(el("rect", {                        // face box
        x: "161", y: "106", width: "78", height: "58", rx: "4",
        fill: "#fbfaf7", stroke: "#c9c3b6", "stroke-width": "2.5",
      }));

      const eyes = el("g");
      eyes.appendChild(el("circle", { cx: "184", cy: "128", r: "5.5", fill: "#2b2b2b" }));
      eyes.appendChild(el("circle", { cx: "216", cy: "128", r: "5.5", fill: "#2b2b2b" }));
      headG.appendChild(eyes);

      const mouth = el("path", {
        d: "M186 143 Q200 155 214 143", fill: "none",
        stroke: "#2b2b2b", "stroke-width": "3.5", "stroke-linecap": "round",
      });
      headG.appendChild(mouth);

      // the CSI camera, mounted at the neck on the real robot
      const camG = el("g");
      camG.appendChild(el("rect", {
        x: "188", y: "196", width: "24", height: "16", rx: "3",
        fill: "#22262c", stroke: "#454c56", "stroke-width": "1.5",
      }));
      const lens = el("circle", { cx: "200", cy: "204", r: "4.5", fill: "#0d4f6e" });
      camG.appendChild(lens);

      scene.appendChild(headG);
      scene.appendChild(camG);
      svg.appendChild(scene);

      this.parts.headG = headG;
      this.parts.eyes = eyes;
      this.parts.mouth = mouth;
      this.parts.lens = lens;

      const wrap = document.createElement("div");
      wrap.className = "robot-wrap";
      wrap.appendChild(svg);

      if (this.opts.showAngles) {
        this.parts.readout = document.createElement("div");
        this.parts.readout.className = "robot-readout";
        wrap.appendChild(this.parts.readout);
      }

      this.mount.innerHTML = "";
      this.mount.appendChild(wrap);
      this.svg = svg;
    }

    // -- state --------------------------------------------------------------
    /** Apply a `{channel: angle}` map. Missing channels keep their last value. */
    setAngles(angles) {
      const n = this.opts.neutral;
      for (const [k, v] of Object.entries(angles || {})) {
        const num = Number(v);
        if (Number.isFinite(num)) this.angles[Number(k)] = num;
      }
      const at = (ch) => (ch === undefined || this.angles[ch] === undefined
        ? n : this.angles[ch]);

      // Head pan is a yaw, which a front view can't rotate. Sliding the head
      // and shifting the eyes a little further (parallax) reads as a turn.
      const pan = (at(this.opts.head) - n) / n;          // -1 .. 1
      const dx = pan * this.opts.headSweep;
      this.parts.headG.setAttribute(
        "transform", `translate(${dx.toFixed(2)} 0) rotate(${(pan * 3).toFixed(2)} 200 190)`);
      this.parts.eyes.setAttribute("transform", `translate(${(dx * 0.32).toFixed(2)} 0)`);

      const L = ArtyomRobot.LAYOUT;
      for (const arm of this.parts.arms) {
        const lift = ArtyomRobot.elevation(at(arm.shoulder), n);
        const upper = ArtyomRobot.limb(lift, arm.side.dir);
        arm.shoulderG.setAttribute("transform", upper.transform);

        // Lifts add up along the limb. The sequences treat a high elbow as
        // "carry on lifting", not as a curl - dance-disco raises ch1 and ch2
        // together to throw one arm overhead - so the forearm's elevation is the
        // shoulder's plus its own. An arm with no elbow channel (the left one,
        // whose elbow servo is parked) stays rigid: the forearm just inherits
        // the shoulder's elevation.
        const bend = arm.elbow === undefined
          ? 0
          : ArtyomRobot.elevation(at(arm.elbow), n) * 0.9;
        const fore = ArtyomRobot.limb(lift + bend, arm.side.dir);
        // Walk down the upper arm to wherever it actually put the elbow.
        const r = (upper.swing * Math.PI) / 180;
        const len = L.upper * upper.foreshorten;
        arm.elbowG.setAttribute(
          "transform",
          `translate(${(-len * Math.sin(r)).toFixed(2)} ${(len * Math.cos(r)).toFixed(2)}) `
          + fore.transform);
      }

      if (this.parts.readout) {
        this.parts.readout.textContent = Object.keys(this.angles)
          .map(Number).sort((a, b) => a - b)
          .map((ch) => `ch${ch} ${Math.round(this.angles[ch])}°`)
          .join("   ");
      }
      return this;
    }

    /** Open the mouth while the robot is speaking. */
    setSpeaking(on) {
      this.parts.mouth.setAttribute(
        "d", on ? "M186 141 Q200 161 214 141 Q200 149 186 141"
                : "M186 143 Q200 155 214 143");
      this.parts.mouth.setAttribute("fill", on ? "#2b2b2b" : "none");
      return this;
    }

    /** Light the camera lens when a face is locked. */
    setFaceSeen(on) {
      this.parts.lens.setAttribute("fill", on ? "#37d67a" : "#0d4f6e");
      return this;
    }
  }

  global.ArtyomRobot = ArtyomRobot;
})(window);
