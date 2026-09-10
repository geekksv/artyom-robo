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
 * straight down; raising the angle lifts the arm outward. Channels listed in
 * `invert` are mirror-mounted on the real robot, so they are mirrored here too
 * and one logical command moves both arms the same visual direction.
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
    // `dir` is which way a raise rotates. SVG rotate() is clockwise, and a limb
    // segment is drawn pointing down (+y), so rotate(+90) swings it to the left.
    // The arm on the viewer's left therefore raises with +1, its mirror with -1;
    // getting this backwards folds both arms across the chest.
    // Segment lengths are chosen so a full 180 raise stays inside the viewBox.
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
      // --- arms (added before the torso so the joints tuck behind the body) -------------------------------------------------------------
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
        elbowG.setAttribute("transform", `translate(0 ${L.upper})`);
        elbowG.appendChild(seg(L.fore, 20));
        elbowG.appendChild(el("rect", {                     // hand
          x: -11, y: L.fore, width: 22, height: L.hand, rx: 4,
          fill: "#f0e6d2", stroke: "#c2b295", "stroke-width": "2",
        }));
        shoulderG.appendChild(elbowG);

        const root = el("g", { transform: `translate(${side.x} ${side.y})` });
        root.appendChild(shoulderG);
        root.appendChild(joint);
        scene.appendChild(root);

        return { shoulderG, elbowG, side, shoulder: pair[0], elbow: pair[1] };
      });

      scene.appendChild(torso);

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

      for (const arm of this.parts.arms) {
        const flip = this.invert.has(arm.shoulder) ? -1 : 1;
        const rot = (at(arm.shoulder) - n) * arm.side.dir * flip;
        arm.shoulderG.setAttribute("transform", `rotate(${rot.toFixed(2)})`);

        if (arm.elbow !== undefined) {
          const eFlip = this.invert.has(arm.elbow) ? -1 : 1;
          const eRot = (at(arm.elbow) - n) * arm.side.dir * eFlip;
          arm.elbowG.setAttribute(
            "transform", `translate(0 ${ArtyomRobot.LAYOUT.upper}) rotate(${eRot.toFixed(2)})`);
        }
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
