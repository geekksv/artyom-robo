"use strict";
/**
 * ArtyomRobot — a small 3-D stand-in for the physical robot.
 *
 * Draws the cardboard build the code actually drives: a spiky-hair head on a pan
 * servo, a box torso in a shirt and tie, and two arms of upper-arm + forearm
 * segments. Feed it the same `{channel: angle}` map the Flask API reports and it
 * mirrors the hardware.
 *
 * Used twice: inside the live web UI (so mock mode on a laptop is visual, and on
 * the Pi you can watch the robot without looking at the robot), and in the
 * browser demo, where it is the only robot there is.
 *
 * ── Why 3-D ──────────────────────────────────────────────────────────────────
 * The arms are flat panels hinged at the top corners of the torso: they swing
 * FORWARD and up, then back down. No sideways travel, nothing behind the body.
 *
 * Drawn face-on that motion is invisible — it comes straight at the viewer — and
 * faking it as sideways rotation produces poses the hardware cannot strike. So
 * the robot is modelled as actual boxes in 3-D and rendered through a
 * three-quarter camera: a shoulder is a rotation about X, the head pan is a
 * rotation about Y, and both read correctly because the view has depth.
 *
 * The renderer is a painter's-algorithm rasteriser in about a hundred lines:
 * build boxes, cull the faces pointing away, shade each by its normal, sort by
 * depth, and write the winning polygons into a fixed pool of SVG elements.
 */
(function (global) {
  const SVG = "http://www.w3.org/2000/svg";
  const RAD = Math.PI / 180;

  const el = (name, attrs = {}) => {
    const node = document.createElementNS(SVG, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };

  // ── camera ────────────────────────────────────────────────────────────────
  // Turned far enough to show one side of every box (so depth is legible) but
  // not so far that the face and tie stop reading straight on.
  const YAW = 26 * RAD;
  const PITCH = 10 * RAD;
  const CY = Math.cos(YAW), SY = Math.sin(YAW);
  const CP = Math.cos(PITCH), SP = Math.sin(PITCH);

  const ORIGIN = [200, 140];   // where the torso's shoulder line lands on screen
  const LIGHT = norm([-0.30, -0.55, 0.78]);
  const AMBIENT = 0.66, DIFFUSE = 0.42;

  function norm(v) {
    const m = Math.hypot(v[0], v[1], v[2]) || 1;
    return [v[0] / m, v[1] / m, v[2] / m];
  }

  /** World point -> [screenX, screenY, depth]. Larger depth is nearer. */
  function project(p) {
    const x1 = p[0] * CY + p[2] * SY;
    const z1 = -p[0] * SY + p[2] * CY;
    return [ORIGIN[0] + x1, ORIGIN[1] + p[1] * CP - z1 * SP, p[1] * SP + z1 * CP];
  }

  /** Direction vector -> camera space (only the depth component is needed). */
  function faceDepthOf(n) {
    const z1 = -n[0] * SY + n[2] * CY;
    return n[1] * SP + z1 * CP;
  }

  // ── transforms (compose right-to-left, like matrices) ─────────────────────
  const T = {
    chain: (...fns) => (p) => fns.reduceRight((q, f) => f(q), p),
    move: (tx, ty, tz) => (p) => [p[0] + tx, p[1] + ty, p[2] + tz],
    rotX: (a) => {
      const c = Math.cos(a), s = Math.sin(a);
      return (p) => [p[0], p[1] * c - p[2] * s, p[1] * s + p[2] * c];
    },
    rotY: (a) => {
      const c = Math.cos(a), s = Math.sin(a);
      return (p) => [p[0] * c + p[2] * s, p[1], -p[0] * s + p[2] * c];
    },
  };
  /** Same transform, minus translation — for normals. */
  const rot = (fn) => (n) => {
    const o = fn([0, 0, 0]);
    const v = fn(n);
    return [v[0] - o[0], v[1] - o[1], v[2] - o[2]];
  };

  // ── the model, in millimetres-ish, origin at the shoulder line ────────────
  const M = {
    torso: { w: 116, h: 186, d: 74 },
    neck: { w: 28, h: 26, d: 26 },
    head: { w: 82, h: 62, d: 56 },
    shoulder: { x: 85, y: 14 },
    upper: { w: 24, h: 62, d: 26 },
    fore: { w: 20, h: 54, d: 22 },
    hand: { w: 22, h: 16, d: 20 },
  };

  const COLOR = {
    card: [201, 162, 115],     // corrugated cardboard
    head: [246, 243, 237],
    hair: [69, 75, 84],
    arm: [63, 160, 224],       // the blue boxes
    hand: [240, 230, 210],
    shirt: [247, 245, 240],
    collar: [231, 226, 216],
    tie: [212, 70, 61],
    belt: [45, 45, 48],
    dark: [34, 38, 44],
    ink: [40, 40, 42],
    lens: [13, 79, 110],
    lensOn: [55, 214, 122],
    visor: [30, 34, 40],
    eye: [96, 200, 255],
    steel: [126, 134, 145],
    screw: [150, 122, 84],
  };

  const shade = (rgb, k) =>
    `rgb(${rgb.map((c) => Math.round(Math.min(255, c * k))).join(",")})`;

  // Box faces, wound so the normal points outward. y runs from 0 (top) down.
  const FACES = [
    { idx: [3, 2, 6, 7], n: [0, 0, 1] },    // front
    { idx: [1, 0, 4, 5], n: [0, 0, -1] },   // back
    { idx: [0, 3, 7, 4], n: [-1, 0, 0] },   // left
    { idx: [2, 1, 5, 6], n: [1, 0, 0] },    // right
    { idx: [0, 1, 2, 3], n: [0, -1, 0] },   // top
    { idx: [7, 6, 5, 4], n: [0, 1, 0] },    // bottom
  ];

  /** Emit the visible, shaded faces of a box into `out`. */
  function box(out, xf, { w, h, d }, rgb, y0 = 0) {
    const hw = w / 2, hd = d / 2, y1 = y0 + h;
    const c = [
      [-hw, y0, -hd], [hw, y0, -hd], [hw, y0, hd], [-hw, y0, hd],
      [-hw, y1, -hd], [hw, y1, -hd], [hw, y1, hd], [-hw, y1, hd],
    ].map((p) => project(xf(p)));
    const nrm = rot(xf);
    for (const f of FACES) {
      const n = norm(nrm(f.n));
      if (faceDepthOf(n) <= 0) continue;             // pointing away — skip it
      const pts = f.idx.map((i) => c[i]);
      out.push({
        points: pts.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" "),
        depth: pts.reduce((s, p) => s + p[2], 0) / 4,
        fill: shade(rgb, AMBIENT + DIFFUSE * Math.max(0, n[0] * LIGHT[0]
          + n[1] * LIGHT[1] + n[2] * LIGHT[2])),
      });
    }
  }

  /** Emit one flat polygon lying in a plane — decals painted on a box face.
   *
   * `anchor` is the centre of the plane the decal is painted on, and the decal
   * takes ITS depth rather than its own centroid's. Painter's algorithm sorts by
   * a single number per polygon, so a small decal near the edge of a large face
   * can average out behind that face's centre and get painted over - which is
   * exactly what swallowed one of the eyes. Sharing the parent's depth and
   * adding a layer bias keeps every decal on top of the face it belongs to, and
   * in the right order among themselves.
   */
  function decal(out, xf, pts3, rgb, k = 1, plane = null, layer = 1) {
    const pts = pts3.map((p) => project(xf(p)));
    // Every decal on one face shares that face's centre as its depth, so only
    // `layer` decides their order. Using each decal's own centroid instead lets
    // the camera's tilt reorder them by height - which buried the collar and the
    // chest camera under the shirt - and letting x through swallowed an eye
    // behind the face it was painted on.
    const base = plane === null
      ? pts.reduce((s, p) => s + p[2], 0) / pts.length
      : project(xf(plane))[2];
    out.push({
      points: pts.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" "),
      depth: base + layer,
      fill: shade(rgb, k),
    });
  }

  const DEFAULTS = {
    head: 0,                      // pan servo
    arms: [[1, 2], [3, 4]],       // [shoulder, elbow] per arm; elbow optional
    invert: [3, 4],
    neutral: 90,
    showAngles: true,
  };

  class ArtyomRobot {
    constructor(mount, opts = {}) {
      this.mount = typeof mount === "string" ? document.querySelector(mount) : mount;
      this.opts = Object.assign({}, DEFAULTS, opts);
      this.angles = {};
      this.speaking = false;
      this.faceSeen = false;
      this._build();
      this.setAngles({});
    }

    // ── servo angle -> joint rotation ───────────────────────────────────────
    /** Degrees of forward elevation. 90 = hanging straight down the side. */
    static elevation(angle, neutral) {
      const d = angle - neutral;
      // Below neutral the arm is already resting against the body, so there is
      // almost nothing left to travel - but every dance alternates 45 with ~160,
      // and flattening the low half would cost half the visible swing. A shallow
      // slope keeps those poses distinct without inventing a backswing.
      return d >= 0 ? Math.min(95, d) : Math.max(-15, d * 0.3);
    }

    // Shoulder and elbow lifts add up, so an arm-up pose needs a ceiling on the
    // total or it folds back over the head - dance-disco drives both joints to
    // 160 at once. 135 puts the hand above the shoulder, still reaching forward.
    static get MAX_ARM_LIFT() { return 135; }

    /** Degrees the head yaws. Pan is a real rotation here, not a fudge. */
    static pan(angle, neutral) {
      return Math.max(-46, Math.min(46, ((angle - neutral) / neutral) * 52));
    }

    _build() {
      const svg = el("svg", {
        viewBox: "0 0 400 400",
        class: "robot-svg",
        role: "img",
        "aria-label": "Artyom robot",
      });

      svg.appendChild(el("ellipse", {
        cx: 200, cy: 336, rx: 96, ry: 15,
        fill: "rgba(0,0,0,.42)", filter: "blur(1px)",
      }));

      // One fixed pool of polygons, rewritten every frame in depth order. No DOM
      // churn, and no reordering: slot i always draws the i-th nearest face.
      this.scene = el("g", { "stroke-linejoin": "round" });
      this.pool = [];
      for (let i = 0; i < 96; i++) {
        const poly = el("polygon", { stroke: "rgba(0,0,0,.28)", "stroke-width": "0.8" });
        this.scene.appendChild(poly);
        this.pool.push(poly);
      }
      svg.appendChild(this.scene);

      const wrap = document.createElement("div");
      wrap.className = "robot-wrap";
      wrap.appendChild(svg);
      if (this.opts.showAngles) {
        this.readout = document.createElement("div");
        this.readout.className = "robot-readout";
        wrap.appendChild(this.readout);
      }
      this.mount.innerHTML = "";
      this.mount.appendChild(wrap);
      this.svg = svg;
    }

    // ── scene assembly ─────────────────────────────────────────────────────
    _faces() {
      const n = this.opts.neutral;
      const at = (ch) => (ch === undefined || this.angles[ch] === undefined
        ? n : this.angles[ch]);
      const out = [];
      const I = (p) => p;

      // torso, and the shirt painted on its front
      box(out, I, M.torso, COLOR.card);
      this._torsoDecals(out);

      // neck, then the head on its pan servo
      box(out, T.move(0, -M.neck.h, 0), M.neck, COLOR.card);
      this._camera(out);
      const yaw = ArtyomRobot.pan(at(this.opts.head), n) * RAD;
      const headXf = T.chain(T.move(0, -M.neck.h, 0), T.rotY(yaw),
        T.move(0, -M.head.h, 0));
      box(out, headXf, M.head, COLOR.head);
      this._headDecals(out, headXf);
      this._hardware(out, headXf);

      // arms. A shoulder is a rotation about X, which swings the limb forward
      // and up - the motion the hardware actually has. Both arms take the same
      // sign, which is what `invert` achieves mechanically on the real robot.
      (this.opts.arms || []).forEach((pair, i) => {
        const sx = i === 0 ? -M.shoulder.x : M.shoulder.x;
        const liftDeg = ArtyomRobot.elevation(at(pair[0]), n);
        const shoulder = T.chain(T.move(sx, M.shoulder.y, 0), T.rotX(liftDeg * RAD));
        box(out, shoulder, M.upper, COLOR.arm);

        // Lifts add up along the limb: the sequences treat a high elbow as
        // "carry on lifting", not as a curl - dance-disco raises ch1 and ch2
        // together to throw one arm overhead. An arm whose elbow servo is
        // parked stays rigid.
        const bend = pair[1] === undefined
          ? 0 : ArtyomRobot.elevation(at(pair[1]), n) * 0.65;
        const total = Math.max(-30, Math.min(ArtyomRobot.MAX_ARM_LIFT, liftDeg + bend));
        const elbow = T.chain(shoulder, T.move(0, M.upper.h, 0),
          T.rotX((total - liftDeg) * RAD));
        box(out, elbow, M.fore, COLOR.arm);
        box(out, T.chain(elbow, T.move(0, M.fore.h, 0)), M.hand, COLOR.hand);
      });

      return out;
    }

    _torsoDecals(out) {
      const z = M.torso.d / 2 + 0.5;           // just proud of the front plane
      const plane = [0, M.torso.h / 2, z];     // shared depth for everything on it
      const P = (x, y) => [x, y, z];
      const paint = (pts, rgb, k, layer) =>
        decal(out, (q) => q, pts, rgb, k, plane, layer);

      paint([P(-49, 12), P(49, 12), P(52, 176), P(-52, 176)], COLOR.shirt, 1, 1);
      paint([P(-22, 12), P(0, 44), P(22, 12), P(13, 10), P(0, 28), P(-13, 10)],
        COLOR.collar, 1, 2);
      paint([P(0, 44), P(11, 58), P(7, 134), P(0, 148), P(-7, 134), P(-11, 58)],
        COLOR.tie, 1, 3);
      paint([P(-51, 156), P(51, 156), P(52, 174), P(-52, 174)], COLOR.belt, 1, 3);
      // breast pocket, and the panel screws that give it away as a machine
      paint([P(20, 62), P(42, 62), P(42, 88), P(20, 88)], COLOR.collar, 0.93, 2);
      for (const [sx, sy] of [[-46, 18], [46, 18], [-48, 170], [48, 170]]) {
        paint(ring(sx, sy, z, 3.4, 8), COLOR.screw, 1, 4);
      }

      // panel seam down the shirt, so the torso reads as a built thing
      paint([P(-52, 150), P(52, 150), P(52, 152), P(-52, 152)], COLOR.collar, 0.8, 2);
    }

    _headDecals(out, xf) {
      const z = M.head.d / 2 + 0.5;
      const plane = [0, M.head.h / 2, z];
      const paint = (pts, rgb, k, layer) => decal(out, xf, pts, rgb, k, plane, layer);

      // the flat cardboard hair cutout, standing up behind the head
      const spikes = [];
      const n = 5, top = -48, base = 4, halfW = 44;
      for (let i = 0; i <= n * 2; i++) {
        spikes.push([-halfW + (i / (n * 2)) * halfW * 2, i % 2 ? top : base, -12]);
      }
      spikes.push([halfW, 10, -12], [-halfW, 10, -12]);
      decal(out, xf, spikes, COLOR.hair, 1, [0, M.head.h / 2, -12], 0);

      // a dark visor, so the face reads as a screen rather than a drawing
      paint([[-30, 12], [30, 12], [30, 34], [-30, 34]].map(([x, y]) => [x, y, z]),
        COLOR.visor, 1, 1);
      paint(ring(-15, 23, z, 6.5), COLOR.eye, 1.35, 2);
      paint(ring(15, 23, z, 6.5), COLOR.eye, 1.35, 2);
      paint(this.speaking ? mouthOpen(z) : mouthSmile(z), COLOR.ink, 1, 1);
      for (const sx of [-32, 32]) paint(ring(sx, 47, z, 3, 8), COLOR.screw, 1, 1);
    }

    /** The CSI camera, on its bracket at the neck. */
    _camera(out) {
      const zf = 22;
      box(out, T.move(0, -16, zf - 7), { w: 30, h: 17, d: 14 }, COLOR.dark);
      const face = [0, -8, zf + 7.5];
      const D = (pts, rgb, k, layer) =>
        decal(out, (q) => q, pts, rgb, k, face, layer);
      D(ring(0, -8, zf + 7.5, 6), COLOR.dark, 0.6, 1);
      D(ring(0, -8, zf + 7.5, 3.8),
        this.faceSeen ? COLOR.lensOn : COLOR.lens, 1.3, 2);
    }

    /** Antenna, ear servos and shoulder discs - the parts that say "machine". */
    _hardware(out, headXf) {
      // antenna: a mast on the head with a bulb on top
      const mast = T.chain(headXf, T.move(16, -26, -6));
      box(out, mast, { w: 4, h: 26, d: 4 }, COLOR.steel);
      box(out, T.chain(mast, T.move(0, -9, 0)), { w: 10, h: 9, d: 10 }, COLOR.lensOn);
      // ear servos, one each side of the head
      for (const sx of [-1, 1]) {
        box(out, T.chain(headXf, T.move(sx * (M.head.w / 2 + 3), 18, 0)),
          { w: 7, h: 22, d: 22 }, COLOR.steel);
      }
      // shoulder servo discs where the arms hinge
      for (const sx of [-1, 1]) {
        box(out, T.move(sx * (M.torso.w / 2 + 6), M.shoulder.y - 9, 0),
          { w: 12, h: 30, d: 30 }, COLOR.steel);
      }
    }

    // ── render ─────────────────────────────────────────────────────────────
    /** Apply a `{channel: angle}` map. Missing channels keep their last value. */
    setAngles(angles) {
      for (const [k, v] of Object.entries(angles || {})) {
        const num = Number(v);
        if (Number.isFinite(num)) this.angles[Number(k)] = num;
      }
      this._draw();
      return this;
    }

    _draw() {
      const faces = this._faces().sort((a, b) => a.depth - b.depth);
      const n = Math.min(faces.length, this.pool.length);
      for (let i = 0; i < n; i++) {
        const p = this.pool[i];
        p.setAttribute("points", faces[i].points);
        p.setAttribute("fill", faces[i].fill);
        p.removeAttribute("display");
      }
      for (let i = n; i < this.pool.length; i++) {
        this.pool[i].setAttribute("display", "none");
      }
      if (this.readout) {
        this.readout.textContent = Object.keys(this.angles)
          .map(Number).sort((a, b) => a - b)
          .map((ch) => `ch${ch} ${Math.round(this.angles[ch])}°`)
          .join("   ");
      }
    }

    /** Open the mouth while the robot is speaking. */
    setSpeaking(on) {
      if (this.speaking === !!on) return this;
      this.speaking = !!on;
      return this.setAngles({});
    }

    /** Light the camera lens when a face is locked. */
    setFaceSeen(on) {
      if (this.faceSeen === !!on) return this;
      this.faceSeen = !!on;
      return this.setAngles({});
    }
  }

  // ── little polygon helpers ────────────────────────────────────────────────
  /** A circle approximated in the z-plane, so it goes through the same pipeline. */
  function ring(cx, cy, z, r, sides = 12) {
    return Array.from({ length: sides }, (_, i) => {
      const a = (i / sides) * Math.PI * 2;
      return [cx + r * Math.cos(a), cy + r * Math.sin(a), z];
    });
  }

  /** A smile, as a curved band sampled into a polygon. */
  function mouthSmile(z, w = 20, drop = 9, thick = 4) {
    const top = [], bot = [];
    for (let i = 0; i <= 10; i++) {
      const t = i / 10;
      const x = -w + 2 * w * t;
      const y = 38 + drop * Math.sin(Math.PI * t);
      top.push([x, y, z]);
      bot.unshift([x, y + thick, z]);
    }
    return top.concat(bot);
  }

  /** An open mouth for the speaking state. */
  function mouthOpen(z, w = 15, h = 11) {
    return Array.from({ length: 14 }, (_, i) => {
      const a = (i / 14) * Math.PI * 2;
      return [w * Math.cos(a), 42 + h * Math.sin(a), z];
    });
  }

  global.ArtyomRobot = ArtyomRobot;
})(window);
