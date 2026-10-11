// Type-specific narrow-body geometry built at true scale (1 unit = 1 m) from published dimensions.
// Reference geometry for the twin; it is not manufacturer CAD. Frame: +x forward (nose), +y up, +z starboard.
// Engine 1 is the left (port, z < 0) engine, engine 2 the right one, as on the flight deck.
import * as T from "three";

export type Part = {
  id: string;
  label: string;
  group: string;
  component?: string;
  description: string;
  internal?: boolean;
};

type Door = { d: number; w: number; h: number; y0: number; side: "both" | "right"; kind: "pax" | "exit" | "cargo" };

export type Profile = {
  key: "a320" | "b738";
  label: string;
  engine: string;
  length: number;
  span: number;
  height: number;
  fusW: number;
  fusH: number;
  // Side silhouette of the nose as (distance from the tip, height above the fuselage axis) control points.
  // Past `len` the section is the full barrel. `wLen` is the length over which the half-width reaches the barrel.
  nose: {
    len: number; radome: number; wLen: number; wExp: number;
    top: [number, number][]; bot: [number, number][];
    // Flight-deck glazing as quads [front-top, aft-top, aft-bottom, front-bottom]. The windshield is given as
    // (distance from the tip, section angle in degrees: 0 = side, 90 = crown); the side windows as (distance, height above the axis).
    windshield: [number, number][]; sliding: [number, number][]; fixed: [number, number][];
  };
  tailLen: number;
  wing: {
    apexD: number; rootY: number; dihedral: number; leSweep: number; teSweepIn: number;
    kinkR: number; tipR: number; rootChord: number; tipChord: number; tcRoot: number; tcTip: number;
    aileron: [number, number]; flapSplit: number; slats: [number, number];
  };
  winglet: { kind: "sharklet" | "blended"; height: number; cant: number; sweep: number; tipChord: number; blend: number };
  engineR: number;
  engineFwd: number;
  engineDrop: number;
  nacelle: { fanR: number; blades: number; scale: number; flatBottom: number };
  htp: { span: number; rootChord: number; tipChord: number; sweep: number; dihedral: number; fromTail: number };
  vtp: { height: number; rootChord: number; tipChord: number; sweep: number; fromTail: number; dorsal: number };
  gear: { noseD: number; mainD: number; track: number; mainTire: number; noseTire: number; centerHeight: number };
  windows: { first: number; last: number; pitch: number; skip: [number, number][] };
  doors: Door[];
  fairings: number[];
  hydraulics: string[];
};

const deg = (v: number) => (v * Math.PI) / 180;

export const profiles: Record<"a320" | "b738", Profile> = {
  a320: {
    key: "a320",
    label: "Airbus A320-200",
    engine: "CFM56-5B",
    length: 37.57,
    span: 35.8,
    height: 11.76,
    fusW: 3.95,
    fusH: 4.14,
    nose: {
      len: 6.8, radome: 1.55, wLen: 4.7, wExp: 2.1,
      top: [[0, -0.2], [0.02, -0.08], [0.06, 0.02], [0.16, 0.14], [0.4, 0.24], [0.9, 0.34], [1.6, 0.48], [2.2, 0.68], [2.8, 1.12], [3.5, 1.62], [4.4, 1.92], [5.5, 2.04]],
      bot: [[0, -0.2], [0.02, -0.34], [0.06, -0.44], [0.16, -0.56], [0.4, -0.68], [0.9, -0.94], [1.6, -1.28], [2.6, -1.66], [3.8, -1.92], [5.0, -2.04]],
      windshield: [[2.12, 87], [3.12, 88], [3.34, 42], [2.4, 43]],
      sliding: [[3.42, 1.1], [3.98, 1.04], [3.98, 0.48], [3.48, 0.52]],
      fixed: [[4.04, 1.02], [4.4, 0.96], [4.4, 0.52], [4.08, 0.49]],
    },
    tailLen: 11.6,
    wing: {
      apexD: 12.9, rootY: -1.28, dihedral: 5.1, leSweep: 27.5, teSweepIn: 3, kinkR: 6.3, tipR: 17.05,
      rootChord: 7.05, tipChord: 1.55, tcRoot: 0.152, tcTip: 0.108, aileron: [13.2, 16.3], flapSplit: 6.3, slats: [2.6, 16.6],
    },
    winglet: { kind: "sharklet", height: 2.43, cant: 12, sweep: 42, tipChord: 0.62, blend: 0.65 },
    engineR: 5.75,
    engineFwd: 2.55,
    engineDrop: 1.42,
    nacelle: { fanR: 0.8675, blades: 36, scale: 1, flatBottom: 0 },
    htp: { span: 12.45, rootChord: 3.55, tipChord: 1.3, sweep: 32, dihedral: 6, fromTail: 7.1 },
    vtp: { height: 5.9, rootChord: 5.9, tipChord: 1.85, sweep: 34, fromTail: 8.6, dorsal: 0.9 },
    gear: { noseD: 5.07, mainD: 17.71, track: 7.59, mainTire: 1.27, noseTire: 0.76, centerHeight: 3.95 },
    windows: { first: 7.4, last: 27.6, pitch: 0.533, skip: [[15.0, 16.85]] },
    doors: [
      { d: 4.95, w: 0.81, h: 1.85, y0: -0.48, side: "both", kind: "pax" },
      { d: 31.0, w: 0.81, h: 1.85, y0: -0.48, side: "both", kind: "pax" },
      { d: 15.35, w: 0.51, h: 1.02, y0: -0.05, side: "both", kind: "exit" },
      { d: 16.45, w: 0.51, h: 1.02, y0: -0.05, side: "both", kind: "exit" },
      { d: 9.2, w: 1.82, h: 1.24, y0: -1.95, side: "right", kind: "cargo" },
      { d: 24.6, w: 1.82, h: 1.24, y0: -1.95, side: "right", kind: "cargo" },
    ],
    fairings: [7.6, 10.6, 13.4],
    hydraulics: ["Green system", "Blue system", "Yellow system"],
  },
  b738: {
    key: "b738",
    label: "Boeing 737-800",
    engine: "CFM56-7B",
    length: 39.47,
    span: 35.79,
    height: 12.55,
    fusW: 3.76,
    fusH: 4.01,
    nose: {
      len: 7.0, radome: 1.75, wLen: 5.0, wExp: 2.0,
      top: [[0, -0.3], [0.02, -0.18], [0.06, -0.08], [0.15, 0.04], [0.35, 0.14], [0.8, 0.25], [1.5, 0.4], [2.2, 0.62], [2.8, 1.12], [3.5, 1.64], [4.4, 1.91], [5.6, 1.99]],
      bot: [[0, -0.3], [0.02, -0.42], [0.06, -0.52], [0.15, -0.64], [0.35, -0.76], [0.8, -1.02], [1.5, -1.36], [2.5, -1.68], [3.6, -1.88], [5.0, -1.975]],
      windshield: [[2.3, 87], [3.3, 88], [3.52, 42], [2.58, 43]],
      sliding: [[3.6, 1.08], [4.1, 1.02], [4.1, 0.46], [3.66, 0.5]],
      fixed: [[4.16, 1.0], [4.5, 0.94], [4.5, 0.5], [4.2, 0.47]],
    },
    tailLen: 12.4,
    wing: {
      apexD: 15.2, rootY: -1.12, dihedral: 6, leSweep: 27, teSweepIn: 2, kinkR: 6.0, tipR: 17.16,
      rootChord: 7.35, tipChord: 1.3, tcRoot: 0.15, tcTip: 0.105, aileron: [13.0, 16.1], flapSplit: 6.0, slats: [6.3, 16.4],
    },
    winglet: { kind: "blended", height: 2.49, cant: 24, sweep: 52, tipChord: 0.6, blend: 1.25 },
    engineR: 4.9,
    engineFwd: 2.95,
    engineDrop: 1.02,
    nacelle: { fanR: 0.7745, blades: 24, scale: 0.94, flatBottom: 0.2 },
    htp: { span: 14.35, rootChord: 3.95, tipChord: 1.4, sweep: 33, dihedral: 7, fromTail: 7.4 },
    vtp: { height: 6.4, rootChord: 6.1, tipChord: 1.9, sweep: 35, fromTail: 9.0, dorsal: 2.6 },
    gear: { noseD: 4.83, mainD: 20.4, track: 5.72, mainTire: 1.12, noseTire: 0.69, centerHeight: 3.05 },
    windows: { first: 7.9, last: 31.6, pitch: 0.508, skip: [[16.7, 18.55]] },
    doors: [
      { d: 5.1, w: 0.86, h: 1.83, y0: -0.45, side: "both", kind: "pax" },
      { d: 33.5, w: 0.76, h: 1.83, y0: -0.45, side: "both", kind: "pax" },
      { d: 17.15, w: 0.51, h: 0.97, y0: -0.05, side: "both", kind: "exit" },
      { d: 18.1, w: 0.51, h: 0.97, y0: -0.05, side: "both", kind: "exit" },
      { d: 8.9, w: 1.29, h: 1.22, y0: -1.85, side: "right", kind: "cargo" },
      { d: 27.6, w: 1.22, h: 1.22, y0: -1.85, side: "right", kind: "cargo" },
    ],
    fairings: [4.2, 8.1, 11.0, 13.8],
    hydraulics: ["System A", "System B", "Standby system"],
  },
};

export function profileFor(type: string): Profile | null {
  if (/B73|737/i.test(type)) return profiles.b738;
  if (/A32|A320/i.test(type)) return profiles.a320;
  return null;
}

// ---------------------------------------------------------------- geometry helpers
const smooth = (t: number) => t * t * (3 - 2 * t);
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const cosSpace = (n: number) => Array.from({ length: n }, (_, i) => 0.5 - 0.5 * Math.cos((Math.PI * i) / (n - 1)));

/** A ruled surface from rings of points. Rings must have the same length; closed rings wrap around. */
function loft(rings: T.Vector3[][], closed = true, capStart = false, capEnd = false): T.BufferGeometry {
  const n = rings[0].length;
  const pos: number[] = [];
  const idx: number[] = [];
  for (const ring of rings) for (const p of ring) pos.push(p.x, p.y, p.z);
  const cols = closed ? n : n - 1;
  for (let i = 0; i < rings.length - 1; i++) {
    for (let j = 0; j < cols; j++) {
      const a = i * n + j;
      const b = i * n + ((j + 1) % n);
      const c = (i + 1) * n + j;
      const d = (i + 1) * n + ((j + 1) % n);
      idx.push(a, c, b, b, c, d);
    }
  }
  const cap = (ring: T.Vector3[], offset: number, flip: boolean) => {
    const center = ring.reduce((s, p) => s.add(p), new T.Vector3()).multiplyScalar(1 / ring.length);
    const ci = pos.length / 3;
    pos.push(center.x, center.y, center.z);
    for (let j = 0; j < n; j++) {
      const a = offset + j;
      const b = offset + ((j + 1) % n);
      if (flip) idx.push(ci, b, a);
      else idx.push(ci, a, b);
    }
  };
  if (capStart) cap(rings[0], 0, false);
  if (capEnd) cap(rings[rings.length - 1], (rings.length - 1) * n, true);
  const g = new T.BufferGeometry();
  g.setAttribute("position", new T.Float32BufferAttribute(pos, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

/** Mirror a geometry across the centerline (z -> -z) and keep faces pointing outward. */
function mirrorZ(g: T.BufferGeometry): T.BufferGeometry {
  const m = g.clone();
  m.applyMatrix4(new T.Matrix4().makeScale(1, 1, -1));
  const index = m.getIndex();
  if (index) {
    const a = index.array as Uint32Array | Uint16Array;
    for (let i = 0; i < a.length; i += 3) {
      const t = a[i + 1];
      a[i + 1] = a[i + 2];
      a[i + 2] = t;
    }
    index.needsUpdate = true;
  }
  m.computeVertexNormals();
  return m;
}

/** Surface of revolution along the x axis from (radius, axial) points; axial grows forward. */
function lathe(points: [number, number][], segments = 72, closeSpline = false): T.BufferGeometry {
  const curve = new T.SplineCurve(points.map(([r, a]) => new T.Vector2(r, a)));
  const pts = closeSpline ? points.map(([r, a]) => new T.Vector2(r, a)) : curve.getPoints(Math.max(48, points.length * 10));
  // LatheGeometry faces point outward only when the profile runs towards +axis; normalise the order.
  if (pts[0].y > pts[pts.length - 1].y) pts.reverse();
  const g = new T.LatheGeometry(pts.map((p) => new T.Vector2(Math.max(p.x, 0.0001), p.y)), segments);
  g.rotateZ(-Math.PI / 2); // lathe axis (y) -> +x
  return g;
}

/** NACA-style thickness with a closed trailing edge, plus a mild aft-loaded camber. */
function airfoil(n: number, tc: number, camber: number, from = 0, to = 1): [number, number][] {
  const xs = cosSpace(n).map((u) => from + (to - from) * u);
  const yt = (x: number) => 5 * tc * (0.2969 * Math.sqrt(x) - 0.126 * x - 0.3516 * x * x + 0.2843 * x ** 3 - 0.1036 * x ** 4);
  const yc = (x: number) => camber * x * (1 - x) * (0.6 + 0.8 * x);
  const upper = xs.map((x) => [x, yc(x) + yt(x)] as [number, number]);
  const lower = [...xs].reverse().map((x) => [x, yc(x) - yt(x)] as [number, number]);
  return [...upper, ...lower.slice(1, lower.length - 1)];
}

// ---------------------------------------------------------------- the model
export function buildAircraft(type: string) {
  const profile = profileFor(type);
  const P = profile ?? profiles.a320;
  const L = P.length;
  const W = P.fusW / 2;
  const H = P.fusH / 2;
  const X = (d: number) => L / 2 - d; // distance from the nose -> model x
  const root = new T.Group();
  const parts: Part[] = [];
  const meshes: T.Mesh[] = [];
  const shells: T.Mesh[] = [];
  const internals: T.Mesh[] = [];
  const fans: T.Group[] = [];
  const lines = new T.Group();
  root.add(lines);

  // Materials: white paint, light-grey wings, polished intake lips, dark glazing.
  const paint = new T.MeshPhysicalMaterial({ color: 0xf6f6f4, roughness: 0.32, metalness: 0.05, clearcoat: 0.55, clearcoatRoughness: 0.25 });
  const wingPaint = new T.MeshPhysicalMaterial({ color: 0xd9dcde, roughness: 0.48, metalness: 0.18, clearcoat: 0.2 });
  const surfacePaint = new T.MeshPhysicalMaterial({ color: 0xcfd3d6, roughness: 0.5, metalness: 0.18 });
  const belly = new T.MeshPhysicalMaterial({ color: 0xe4e6e7, roughness: 0.42, metalness: 0.08, clearcoat: 0.3 });
  const tailPaint = new T.MeshPhysicalMaterial({ color: 0x2b4560, roughness: 0.38, metalness: 0.08, clearcoat: 0.5 });
  const lip = new T.MeshStandardMaterial({ color: 0xd4d7da, roughness: 0.16, metalness: 1 });
  const titanium = new T.MeshStandardMaterial({ color: 0x5b5f64, roughness: 0.34, metalness: 0.85 });
  const darkMetal = new T.MeshStandardMaterial({ color: 0x3b3e42, roughness: 0.45, metalness: 0.7 });
  const coreMetal = new T.MeshStandardMaterial({ color: 0xa9adb1, roughness: 0.32, metalness: 0.85 });
  const glass = new T.MeshPhysicalMaterial({ color: 0x1b2229, roughness: 0.08, metalness: 0.2, clearcoat: 1 });
  const rubber = new T.MeshStandardMaterial({ color: 0x1e1f20, roughness: 0.92 });
  const chrome = new T.MeshStandardMaterial({ color: 0xc8ccd0, roughness: 0.18, metalness: 1 });
  const hub = new T.MeshStandardMaterial({ color: 0x9da2a6, roughness: 0.4, metalness: 0.8 });
  const carbon = new T.MeshStandardMaterial({ color: 0x3d3430, roughness: 0.7, metalness: 0.3 });
  const equipment = new T.MeshStandardMaterial({ color: 0x8a949c, roughness: 0.5, metalness: 0.5 });
  const lineMat = new T.LineBasicMaterial({ color: 0x9aa1a7, transparent: true, opacity: 0.85 });

  const part = (id: string, label: string, group: string, description: string, component?: string, internal = false) => {
    const existing = parts.find((p) => p.id === id);
    if (existing) return existing;
    const item: Part = { id, label, group, description, component, internal };
    parts.push(item);
    return item;
  };
  const add = (g: T.BufferGeometry, m: T.Material, item: Part, opts: { shell?: boolean; parent?: T.Object3D } = {}) => {
    const o = new T.Mesh(g, m.clone());
    o.castShadow = true;
    o.receiveShadow = true;
    o.userData.part = item.id;
    (opts.parent ?? root).add(o);
    meshes.push(o);
    if (opts.shell) shells.push(o);
    if (item.internal) {
      o.visible = false;
      internals.push(o);
    }
    return o;
  };
  const polyline = (pts: T.Vector3[], closed = true) => {
    const g = new T.BufferGeometry().setFromPoints(closed ? [...pts, pts[0]] : pts);
    lines.add(new T.Line(g, lineMat));
  };

  // ------------------------------------------------------------ fuselage
  // Top and bottom contours plus half-width, as functions of distance from the nose.
  const N = P.nose;
  const tailStart = L - P.tailLen;
  // Monotone cubic through control points, so the silhouette cannot overshoot between them.
  const pchip = (pts: [number, number][]) => {
    const n = pts.length;
    const h: number[] = [];
    const dl: number[] = [];
    for (let i = 0; i < n - 1; i++) {
      h.push(pts[i + 1][0] - pts[i][0]);
      dl.push((pts[i + 1][1] - pts[i][1]) / h[i]);
    }
    const m: number[] = new Array(n).fill(0);
    m[0] = dl[0];
    m[n - 1] = dl[n - 2];
    for (let i = 1; i < n - 1; i++) {
      if (dl[i - 1] * dl[i] <= 0) continue;
      const w1 = 2 * h[i] + h[i - 1];
      const w2 = h[i] + 2 * h[i - 1];
      m[i] = (w1 + w2) / (w1 / dl[i - 1] + w2 / dl[i]);
    }
    return (x: number) => {
      if (x <= pts[0][0]) return pts[0][1];
      if (x >= pts[n - 1][0]) return pts[n - 1][1];
      let i = 0;
      while (x > pts[i + 1][0]) i++;
      const t = (x - pts[i][0]) / h[i];
      const t2 = t * t;
      const t3 = t2 * t;
      return (2 * t3 - 3 * t2 + 1) * pts[i][1] + (t3 - 2 * t2 + t) * h[i] * m[i] + (-2 * t3 + 3 * t2) * pts[i + 1][1] + (t3 - t2) * h[i] * m[i + 1];
    };
  };
  const noseTop = pchip([...N.top, [N.len, H]]);
  const noseBot = pchip([...N.bot, [N.len, -H]]);
  const noseW = (d: number) => {
    const u = Math.min(Math.max(d / N.wLen, 0), 1);
    return W * (1 - (1 - u) ** N.wExp) ** (1 / N.wExp);
  };
  const contour = (d: number) => {
    if (d <= N.len) return { top: noseTop(d), bot: noseBot(d), w: noseW(d) };
    if (d < tailStart) return { top: H, bot: -H, w: W };
    const u = Math.min((d - tailStart) / P.tailLen, 1);
    return { top: H * (1 - 0.2 * u * u), bot: -H + 1.43 * H * u ** 1.32, w: W * (1 - 0.86 * u ** 1.22) };
  };
  const SE = 2.12; // slightly fuller than an ellipse, like a pressure fuselage
  const ringPoint = (d: number, th: number, off = 0) => {
    const c = contour(d);
    const cy = (c.top + c.bot) / 2;
    const h = (c.top - c.bot) / 2;
    const co = Math.cos(th);
    const si = Math.sin(th);
    const z = (c.w + off) * Math.sign(co) * Math.abs(co) ** (2 / SE);
    const y = cy + (h + off) * Math.sign(si) * Math.abs(si) ** (2 / SE);
    return new T.Vector3(X(d), y, z);
  };
  const thetaAt = (d: number, y: number, right = true) => {
    const c = contour(d);
    const cy = (c.top + c.bot) / 2;
    const h = (c.top - c.bot) / 2;
    const s = T.MathUtils.clamp((y - cy) / h, -0.999, 0.999);
    const si = Math.sign(s) * Math.abs(s) ** (SE / 2);
    const th = Math.asin(si);
    return right ? th : Math.PI - th;
  };
  const ds: number[] = [];
  for (let i = 0; i <= 64; i++) ds.push(N.len * (1 - Math.cos((Math.PI / 2) * (i / 64))));
  for (let d = N.len + 0.5; d < tailStart; d += 0.5) ds.push(d);
  for (let i = 0; i <= 36; i++) ds.push(tailStart + P.tailLen * (i / 36));
  const seg = 72;
  const fusRings = [...new Set(ds.map((d) => Math.max(d, 0.004).toFixed(4)))].map(Number).sort((a, b) => a - b)
    .map((d) => Array.from({ length: seg }, (_, j) => ringPoint(d, (2 * Math.PI * j) / seg)));
  const fuselage = part("fuselage", "Fuselage pressure shell", "Airframe",
    `Semi-monocoque pressure fuselage, ${P.fusW} m wide and ${P.fusH} m high, with the type's nose and upswept tail cone.`);
  add(loft(fusRings, true, true, true), paint, fuselage, { shell: true });

  // Radome (weather radar): the first part of the nose, a shade greyer, with the joint to the metal skin drawn.
  const radome = part("radome", "Radome and weather radar", "Systems", "Composite nose radome over the weather-radar antenna (ATA 34).", "avionics");
  const radomeRings = ds.filter((d) => d > 0.004 && d <= N.radome).map((d) => Array.from({ length: seg }, (_, j) => ringPoint(d, (2 * Math.PI * j) / seg, 0.006)));
  radomeRings.push(Array.from({ length: seg }, (_, j) => ringPoint(N.radome, (2 * Math.PI * j) / seg, 0.006)));
  if (radomeRings.length > 2) add(loft(radomeRings, true, true, false), new T.MeshPhysicalMaterial({ color: 0xe6e8e8, roughness: 0.4, clearcoat: 0.4 }), radome);
  polyline(Array.from({ length: seg }, (_, j) => ringPoint(N.radome, (2 * Math.PI * j) / seg, 0.012)));

  // Flight-deck glazing: each pane is a patch in (distance, section angle) space, so it follows the nose surface.
  const cockpit = part("cockpit", "Flight deck windows", "Airframe", "Windshield and side windows of the flight deck, following the nose surface.");
  const patchDT = (corners: [number, number][], off: number, right: boolean, nu = 10, nv = 8) => {
    const rings: T.Vector3[][] = [];
    for (let i = 0; i <= nu; i++) {
      const u = i / nu;
      const top = [lerp(corners[0][0], corners[1][0], u), lerp(corners[0][1], corners[1][1], u)];
      const bot = [lerp(corners[3][0], corners[2][0], u), lerp(corners[3][1], corners[2][1], u)];
      const ring: T.Vector3[] = [];
      for (let j = 0; j <= nv; j++) {
        const v = j / nv;
        const d = lerp(top[0], bot[0], v);
        const a = deg(Math.min(lerp(top[1], bot[1], v), 89.6));
        ring.push(ringPoint(d, right ? a : Math.PI - a, off));
      }
      rings.push(ring);
    }
    return loft(rings, false);
  };
  const grow = (c: [number, number][], dd: number, da: number): [number, number][] => {
    const cd = c.reduce((s, p) => s + p[0], 0) / c.length;
    const ca = c.reduce((s, p) => s + p[1], 0) / c.length;
    return c.map(([d, a]) => [d + Math.sign(d - cd) * dd, a + Math.sign(a - ca) * da]);
  };
  const frame = new T.MeshStandardMaterial({ color: 0x5c6268, roughness: 0.5, metalness: 0.4, side: T.DoubleSide });
  const cockpitGlass = new T.MeshPhysicalMaterial({ color: 0x16212c, roughness: 0.04, metalness: 0.35, clearcoat: 1, clearcoatRoughness: 0.02, envMapIntensity: 1.4, side: T.DoubleSide });
  const toAngles = (quad: [number, number][]): [number, number][] => quad.map(([d, y]) => [d, (thetaAt(d, y, true) * 180) / Math.PI]);
  for (const right of [true, false]) {
    for (const quad of [N.windshield, toAngles(N.sliding), toAngles(N.fixed)]) {
      add(patchDT(grow(quad, 0.035, 1.6), 0.011, right, 8, 6), frame, cockpit);
      add(patchDT(quad, 0.017, right), cockpitGlass, cockpit);
    }
  }

  // Cabin windows: rounded, sitting on the shell on both sides.
  const cabinWindow = new T.ShapeGeometry(
    (() => {
      const s = new T.Shape();
      const w = 0.115;
      const h = 0.17;
      const r = 0.07;
      s.moveTo(-w + r, -h);
      s.lineTo(w - r, -h);
      s.quadraticCurveTo(w, -h, w, -h + r);
      s.lineTo(w, h - r);
      s.quadraticCurveTo(w, h, w - r, h);
      s.lineTo(-w + r, h);
      s.quadraticCurveTo(-w, h, -w, h - r);
      s.lineTo(-w, -h + r);
      s.quadraticCurveTo(-w, -h, -w + r, -h);
      return s;
    })(),
    6,
  );
  const cabinMat = glass.clone();
  const winPos: number[] = [];
  for (let d = P.windows.first; d <= P.windows.last; d += P.windows.pitch) {
    if (P.windows.skip.some(([a, b]) => d > a && d < b)) continue;
    winPos.push(d);
  }
  const cabinWindows = new T.InstancedMesh(cabinWindow, cabinMat, winPos.length * 2);
  const dummy = new T.Object3D();
  let k = 0;
  for (const right of [true, false]) {
    for (const d of winPos) {
      const th = thetaAt(d, 0.22, right);
      const p = ringPoint(d, th, 0.012);
      const n = ringPoint(d, th, 1).sub(ringPoint(d, th, 0)).normalize();
      dummy.position.copy(p);
      dummy.lookAt(p.clone().add(n));
      dummy.updateMatrix();
      cabinWindows.setMatrixAt(k++, dummy.matrix);
    }
  }
  cabinWindows.userData.part = "fuselage";
  root.add(cabinWindows);

  // Doors and exits: panel lines drawn on the skin.
  const doorsPart = part("doors", "Passenger doors and exits", "Airframe", "Plug-type passenger doors, over-wing emergency exits and lower-deck cargo doors (panel outlines).");
  for (const door of P.doors) {
    for (const right of door.side === "both" ? [true, false] : [true]) {
      const pts: T.Vector3[] = [];
      const r = Math.min(door.w, door.h) * 0.22;
      const corners = [
        [door.d - door.w / 2, door.y0 + door.h],
        [door.d + door.w / 2, door.y0 + door.h],
        [door.d + door.w / 2, door.y0],
        [door.d - door.w / 2, door.y0],
      ];
      for (let c = 0; c < 4; c++) {
        const [d0, y0] = corners[c];
        const [d1, y1] = corners[(c + 1) % 4];
        for (let s = 0; s < 10; s++) {
          const t = s / 10;
          let d = lerp(d0, d1, t);
          let y = lerp(y0, y1, t);
          // round the corners slightly
          const nearStart = t < 0.15 ? (0.15 - t) / 0.15 : 0;
          if (nearStart) {
            d += (c === 0 ? r : c === 2 ? -r : 0) * nearStart * 0.4;
            y += (c === 1 ? -r : c === 3 ? r : 0) * nearStart * 0.4;
          }
          pts.push(ringPoint(d, thetaAt(d, y, right), 0.006));
        }
      }
      polyline(pts);
    }
  }
  void doorsPart;

  // Wing-to-body fairing: the long belly pod around the wing box.
  const fairPart = part("belly", "Wing-to-body fairing", "Airframe", "Composite belly fairing around the centre wing box and main-gear bays.");
  const fd0 = P.wing.apexD - 2.2;
  const fd1 = P.wing.apexD + P.wing.rootChord + 2.6;
  const fairRings: T.Vector3[][] = [];
  for (let i = 0; i <= 30; i++) {
    const u = i / 30;
    const d = lerp(fd0, fd1, u);
    const bulge = Math.sin(Math.PI * Math.min(1, u * 1.15)) ** 0.6;
    const ring: T.Vector3[] = [];
    for (let j = 0; j <= 24; j++) {
      const a = Math.PI + (Math.PI * j) / 24; // lower half
      const z = Math.cos(a) * (W * 1.02 + 0.32 * bulge);
      const y = -0.15 * H + Math.sin(a) * (0.85 * H + 0.42 * bulge);
      ring.push(new T.Vector3(X(d), y, z));
    }
    fairRings.push(ring);
  }
  add(loft(fairRings, false), belly, fairPart, { shell: true });

  // ------------------------------------------------------------ wings
  const Wg = P.wing;
  const tl = Math.tan(deg(Wg.leSweep));
  const dLE = (r: number) => Wg.apexD + r * tl;
  const teKink = Wg.apexD + Wg.rootChord + Wg.kinkR * Math.tan(deg(Wg.teSweepIn));
  const dTE = (r: number) =>
    r <= Wg.kinkR
      ? Wg.apexD + Wg.rootChord + r * Math.tan(deg(Wg.teSweepIn))
      : lerp(teKink, dLE(Wg.tipR) + Wg.tipChord, (r - Wg.kinkR) / (Wg.tipR - Wg.kinkR));
  const chord = (r: number) => dTE(r) - dLE(r);
  const wingY = (r: number) => Wg.rootY + r * Math.tan(deg(Wg.dihedral));
  const tcAt = (r: number) => lerp(Wg.tcRoot, Wg.tcTip, smooth(Math.min(1, r / Wg.tipR)));
  const twist = (r: number) => deg(lerp(2.5, -0.5, r / Wg.tipR));
  const section = (r: number, from: number, to: number, n = 22) => {
    const c = chord(r);
    const a = twist(r);
    return airfoil(n, tcAt(r), 0.022, from, to).map(([f, t]) => {
      const d = dLE(r) + f * c;
      const y = wingY(r) + t * c + (0.25 - f) * c * Math.sin(a);
      return new T.Vector3(X(d), y, r);
    });
  };
  const rootR = W * 0.55;
  const wingRange = (r0: number, r1: number, from: number, to: number, steps: number) => {
    const rings: T.Vector3[][] = [];
    for (let i = 0; i <= steps; i++) rings.push(section(lerp(r0, r1, i / steps), from, to));
    return rings;
  };
  const sides: [boolean, string][] = [[true, "right"], [false, "left"]];
  const placeBoth = (g: T.BufferGeometry, m: T.Material, item: (side: string) => Part, shell = false) => {
    for (const [right, side] of sides) add(right ? g : mirrorZ(g), m, item(side), { shell });
  };
  const wingBox = (side: string) => part(`wing_${side}`, `${side === "left" ? "Left" : "Right"} wing box`, "Flight controls",
    `Two-spar wing box with integral fuel tank; ${Wg.leSweep}° leading-edge sweep and ${Wg.dihedral}° dihedral.`);
  // main box between slats and trailing-edge devices, in spanwise pieces so the devices read as separate surfaces
  placeBoth(loft(wingRange(rootR, Wg.slats[0], 0, 0.74, 6), true), wingPaint, wingBox);
  placeBoth(loft(wingRange(Wg.slats[0], Wg.tipR, 0.12, 0.74, 20), true), wingPaint, wingBox);
  placeBoth(loft(wingRange(Wg.aileron[1], Wg.tipR, 0.74, 1, 3), true, false, false), wingPaint, wingBox);
  const slats = (side: string) => part(`slats_${side}`, "Leading-edge slats", "Flight controls",
    P.key === "a320" ? "Five slats per wing, extended for take-off and landing." : "Leading-edge slats outboard of the engine and Krueger flaps inboard.");
  placeBoth(loft(wingRange(Wg.slats[0], Wg.slats[1], 0, 0.12, 18), true), surfacePaint, slats);
  placeBoth(loft(wingRange(Wg.slats[1], Wg.tipR, 0, 0.12, 2), true), wingPaint, wingBox);
  const flaps = (side: string) => part(`flaps_${side}`, "Trailing-edge flaps", "Flight controls",
    P.key === "a320" ? "Single-slotted Fowler flaps, inboard and outboard of the kink." : "Double-slotted inboard and outboard flaps.");
  placeBoth(loft(wingRange(rootR, Wg.flapSplit, 0.74, 1, 6), true), surfacePaint, flaps);
  placeBoth(loft(wingRange(Wg.flapSplit + 0.05, Wg.aileron[0], 0.74, 1, 8), true), surfacePaint, flaps);
  const aileron = (side: string) => part(`aileron_${side}`, "Aileron", "Flight controls", "Outboard aileron for roll control, with spoilers ahead of the flaps.");
  placeBoth(loft(wingRange(Wg.aileron[0] + 0.05, Wg.aileron[1], 0.74, 1, 6), true), surfacePaint, aileron);
  // spoiler panel lines on the upper surface
  for (const right of [true, false]) {
    const s = right ? 1 : -1;
    for (const f of [0.62]) {
      const pts: T.Vector3[] = [];
      for (let i = 0; i <= 24; i++) {
        const r = lerp(Wg.kinkR * 0.45, Wg.aileron[0], i / 24);
        const c = chord(r);
        const d = dLE(r) + f * c;
        pts.push(new T.Vector3(X(d), wingY(r) + 0.06 * c * tcAt(r) * 10 * 0.5 + 0.01, s * r));
      }
      polyline(pts, false);
    }
  }

  // Winglets (A320 sharklet / 737 blended winglet): the tip section sweeps up along an arc.
  const WL = P.winglet;
  const winglet = (side: string) => part(`winglet_${side}`, WL.kind === "sharklet" ? "Sharklet" : "Blended winglet", "Flight controls",
    WL.kind === "sharklet" ? "2.4 m sharklet wing-tip device." : "2.5 m blended winglet.");
  {
    const rings: T.Vector3[][] = [];
    const phiMax = deg(90 - WL.cant);
    const steps = 18;
    const rTip = Wg.tipR;
    const yTip = wingY(rTip);
    const arcLen = WL.blend * phiMax;
    const straight = Math.max(0.2, (WL.height - WL.blend * (1 - Math.cos(phiMax))) / Math.sin(phiMax));
    const total = arcLen + straight;
    for (let i = 0; i <= steps; i++) {
      const s = (i / steps) * total;
      let r: number, y: number, phi: number;
      if (s <= arcLen) {
        phi = s / WL.blend;
        r = rTip + WL.blend * Math.sin(phi);
        y = yTip + WL.blend * (1 - Math.cos(phi));
      } else {
        phi = phiMax;
        const e = s - arcLen;
        r = rTip + WL.blend * Math.sin(phiMax) + e * Math.cos(phiMax);
        y = yTip + WL.blend * (1 - Math.cos(phiMax)) + e * Math.sin(phiMax);
      }
      const u = s / total;
      const c = lerp(Wg.tipChord, WL.tipChord, smooth(u));
      const le = dLE(rTip) + s * Math.tan(deg(WL.sweep)) * 0.82;
      const tc = lerp(Wg.tcTip, 0.085, u);
      rings.push(airfoil(20, tc, lerp(0.022, 0.0, u), 0, 1).map(([f, t]) => {
        const d = le + f * c;
        const th = t * c;
        return new T.Vector3(X(d), y + th * Math.cos(phi), r - th * Math.sin(phi));
      }));
    }
    const g = loft(rings, true, false, true);
    placeBoth(g, tailPaint, winglet);
  }

  // Flap-track fairings ("canoes") under the trailing edge.
  const canoe = (side: string) => part(`fairings_${side}`, "Flap-track fairings", "Flight controls", "Fairings over the flap support tracks and actuators.");
  const canoeGeo = (r: number) => {
    const c = chord(r);
    const len = 0.62 * c + 1.1;
    const g = lathe([[0.001, 0], [0.12, -0.25], [0.2, -0.8], [0.21, -len * 0.6], [0.12, -len * 0.92], [0.001, -len]]);
    g.translate(X(dLE(r) + 0.55 * c), wingY(r) - 0.06 * c - 0.12, r);
    return g;
  };
  for (const r of P.fairings) placeBoth(canoeGeo(r), wingPaint, canoe);

  // ------------------------------------------------------------ engines
  const NC = P.nacelle;
  const Rf = NC.fanR;
  const engineGroup = (right: boolean) => {
    const comp = right ? "engine_2" : "engine_1";
    const name = right ? "Engine 2 (right)" : "Engine 1 (left)";
    const g = new T.Group();
    const r = P.engineR;
    const dInlet = dLE(r) - P.engineFwd;
    const lowerWing = wingY(r) - tcAt(r) * chord(r) * 0.5;
    g.position.set(X(dInlet), lowerWing - P.engineDrop, right ? r : -r);
    root.add(g);
    const sc = NC.scale;
    const nac = part(`${comp}_nacelle`, `${name} nacelle`, "Engines",
      `${P.engine} high-bypass turbofan, ${(Rf * 2).toFixed(2)} m fan, ${NC.blades} fan blades. Fan cowl and inlet.`, comp);
    const lipPart = part(`${comp}_inlet`, `${name} inlet lip`, "Engines", "Anti-iced inlet lip (polished aluminium).", comp);
    const rev = part(`${comp}_reverser`, `${name} thrust reverser`, "Engines", "Translating-sleeve cascade thrust reverser around the fan duct.", comp);
    const fanPart = part(`${comp}_fan`, `${name} fan`, "Engines", `${NC.blades}-blade fan and spinner (low-pressure spool, sensor Nf).`, comp);
    const exhaust = part(`${comp}_exhaust`, `${name} core exhaust`, "Engines", "Core cowl, primary nozzle and exhaust plug (sensor T50 is measured upstream, at the LPT exit).", comp);
    const pylon = part(`${comp}_pylon`, `${name} pylon`, "Engines", "Engine pylon (strut) carrying the engine under the wing.", comp);
    const core = part(`${comp}_core`, `${name} core (cutaway)`, "Engines",
      "Booster, 9-stage high-pressure compressor, annular combustor, single-stage HP turbine and 4-stage LP turbine.", comp, true);
    const flatten = (geo: T.BufferGeometry) => {
      if (!NC.flatBottom) return geo;
      const p = geo.getAttribute("position");
      for (let i = 0; i < p.count; i++) {
        const x = p.getX(i);
        const y = p.getY(i);
        if (y < 0) p.setY(i, y * (1 - NC.flatBottom * T.MathUtils.clamp(1 - (-x - 0.6) / 1.4, 0, 1)));
      }
      geo.computeVertexNormals();
      return geo;
    };
    const s = (v: number) => v * Rf * sc;
    const a = (v: number) => v * sc;
    const lipGeo = flatten(lathe([[s(1.02), a(-0.08)], [s(1.06), a(-0.015)], [s(1.1), a(0)], [s(1.15), a(-0.06)], [s(1.19), a(-0.16)]]));
    add(lipGeo, lip, lipPart, { parent: g });
    const cowl = flatten(lathe([[s(1.19), a(-0.16)], [s(1.25), a(-0.45)], [s(1.28), a(-1.0)], [s(1.27), a(-1.55)], [s(1.25), a(-1.85)]]));
    add(cowl, paint, nac, { parent: g, shell: true });
    const sleeve = flatten(lathe([[s(1.25), a(-1.85)], [s(1.21), a(-2.3)], [s(1.14), a(-2.75)], [s(1.08), a(-3.0)], [s(1.06), a(-3.02)], [s(1.04), a(-2.7)]]));
    add(sleeve, paint, rev, { parent: g, shell: true });
    const duct = flatten(lathe([[s(1.02), a(-0.08)], [s(0.99), a(-0.35)], [s(1.0), a(-0.75)], [s(1.0), a(-0.95)]]));
    add(duct, new T.MeshStandardMaterial({ color: 0xb9bdc0, roughness: 0.55, metalness: 0.4, side: T.BackSide }), lipPart, { parent: g });
    // panel lines on the cowl
    for (const ax of [-0.45, -1.85]) {
      const pts: T.Vector3[] = [];
      const rr = s(ax === -0.45 ? 1.255 : 1.252);
      for (let i = 0; i <= 48; i++) {
        const t = (i / 48) * Math.PI * 2;
        let y = Math.sin(t) * rr;
        if (y < 0 && NC.flatBottom) y *= 1 - NC.flatBottom * T.MathUtils.clamp(1 - (-a(ax) - 0.6) / 1.4, 0, 1);
        pts.push(new T.Vector3(a(ax), y, Math.cos(t) * rr));
      }
      const line = new T.Line(new T.BufferGeometry().setFromPoints(pts), lineMat);
      g.add(line);
    }
    // fan: dark face, spinner and blades
    const face = new T.CircleGeometry(s(0.995), 48);
    face.rotateY(Math.PI / 2);
    face.translate(a(-1.0), 0, 0);
    add(face, darkMetal, fanPart, { parent: g });
    const fan = new T.Group();
    fan.position.set(a(-0.86), 0, 0);
    g.add(fan);
    const blade = new T.BoxGeometry(0.018, s(0.66), s(0.17));
    blade.translate(0, s(0.31 + 0.33), 0);
    for (let i = 0; i < NC.blades; i++) {
      const bg = blade.clone();
      bg.rotateY(deg(-38));
      bg.rotateX((i / NC.blades) * Math.PI * 2);
      add(bg, titanium, fanPart, { parent: fan });
    }
    fans.push(fan);
    const spinner = lathe([[s(0.3), a(-0.95)], [s(0.29), a(-0.82)], [s(0.2), a(-0.6)], [s(0.08), a(-0.42)], [0.001, a(-0.36)]]);
    add(spinner, darkMetal, fanPart, { parent: g });
    // core cowl, primary nozzle and plug
    add(lathe([[s(0.74), a(-2.55)], [s(0.75), a(-2.9)], [s(0.66), a(-3.55)], [s(0.53), a(-4.05)]]), coreMetal, exhaust, { parent: g });
    add(lathe([[s(0.47), a(-3.9)], [s(0.4), a(-4.2)], [s(0.24), a(-4.65)], [0.001, a(-4.95)]]), darkMetal, exhaust, { parent: g });
    // internal core for the cutaway: compressor drum, combustor and turbine discs
    add(lathe([[s(0.32), a(-1.0)], [s(0.55), a(-1.2)], [s(0.5), a(-2.0)], [s(0.45), a(-2.5)], [s(0.58), a(-2.65)], [s(0.6), a(-3.2)], [s(0.4), a(-3.6)]]), titanium, core, { parent: g });
    // pylon: a tapered strut from the cowl top to the wing lower surface, running aft over the core
    const pyl = new T.Shape();
    const top = lowerWing - g.position.y + 0.25;
    pyl.moveTo(a(-0.55), s(1.18));
    pyl.lineTo(a(-0.2), top - 0.3);
    pyl.quadraticCurveTo(a(0.1), top, a(-0.4), top);
    pyl.lineTo(a(-3.9), top);
    pyl.lineTo(a(-4.3), s(0.62));
    pyl.quadraticCurveTo(a(-3.2), s(0.75), a(-2.6), s(1.02));
    pyl.lineTo(a(-0.55), s(1.18));
    const pg = new T.ExtrudeGeometry(pyl, { depth: 0.34, bevelEnabled: true, bevelThickness: 0.06, bevelSize: 0.05, bevelSegments: 3, curveSegments: 12 });
    pg.translate(0, 0, -0.17);
    add(pg, paint, pylon, { parent: g });
    return g;
  };
  engineGroup(false);
  engineGroup(true);

  // ------------------------------------------------------------ empennage
  const HT = P.htp;
  const hRootD = L - HT.fromTail;
  const hy = (contour(hRootD + HT.rootChord * 0.4).top + contour(hRootD + HT.rootChord * 0.4).bot) / 2 + 0.15;
  const htpSection = (r: number) => {
    const u = r / (HT.span / 2);
    const c = lerp(HT.rootChord, HT.tipChord, u);
    const le = hRootD + r * Math.tan(deg(HT.sweep));
    return airfoil(18, lerp(0.11, 0.09, u), 0, 0, 1).map(([f, t]) => new T.Vector3(X(le + f * c), hy + r * Math.tan(deg(HT.dihedral)) + t * c, r));
  };
  const htpPart = (side: string) => part(`htp_${side}`, "Horizontal stabilizer and elevator", "Flight controls",
    `Trimmable horizontal stabilizer, ${HT.span} m span, with elevator.`);
  {
    const rings = Array.from({ length: 13 }, (_, i) => htpSection(lerp(0.3, HT.span / 2, i / 12)));
    placeBoth(loft(rings, true, false, true), wingPaint, htpPart);
  }
  const VT = P.vtp;
  const vRootD = L - VT.fromTail;
  const vBase = (d: number) => contour(d).top - 0.25;
  const vtpPart = part("vtp", "Vertical fin and rudder", "Flight controls", `Vertical stabilizer and rudder, ${VT.height} m above the fuselage.`);
  {
    const rings: T.Vector3[][] = [];
    const steps = 16;
    for (let i = 0; i <= steps; i++) {
      const u = i / steps;
      const hgt = u * VT.height;
      // dorsal fillet: the leading edge sweeps much further forward near the root
      const dors = VT.dorsal * Math.max(0, 1 - u / 0.18) ** 2;
      const le = vRootD + hgt * Math.tan(deg(VT.sweep)) - dors;
      const c = lerp(VT.rootChord, VT.tipChord, u) + dors;
      const yb = vBase(vRootD + VT.rootChord * 0.5) + hgt;
      rings.push(airfoil(18, lerp(0.11, 0.095, u) * (VT.rootChord / c), 0, 0, 1).map(([f, t]) => new T.Vector3(X(le + f * c), yb, t * c)));
    }
    add(loft(rings, true, false, true), tailPaint, vtpPart);
  }
  const apu = part("apu", "Tail cone and APU exhaust", "Airframe", "Auxiliary power unit in the tail cone; exhaust at the tip.");
  {
    const g = new T.CylinderGeometry(0.16, 0.18, 0.12, 24, 1, true);
    g.rotateZ(Math.PI / 2);
    const c = contour(L - 0.03);
    g.translate(X(L) - 0.02, (c.top + c.bot) / 2, 0);
    add(g, darkMetal, apu);
  }

  // ------------------------------------------------------------ landing gear (down)
  const G = P.gear;
  const groundY = -G.centerHeight;
  const gearPart = part("landing_gear", "Landing gear", "Landing gear", "Nose gear and two main gears with shock struts, braces and wheels.", "landing_gear");
  const noseGear = part("nose_gear", "Nose landing gear", "Landing gear", "Steerable twin-wheel nose gear.", "landing_gear");
  const brakesPart = part("brakes", "Wheel brakes", "Landing gear", "Multi-disc brakes on the four main wheels.", "brakes");
  const tyre = (radius: number, width: number) => {
    const g = new T.TorusGeometry(radius - width * 0.42, width * 0.46, 14, 36);
    g.scale(1, 1, 1.15);
    return g;
  };
  const wheel = (cx: number, cz: number, radius: number, width: number, item: Part, brake: boolean) => {
    const t = tyre(radius, width);
    t.translate(cx, groundY + radius, cz);
    add(t, rubber, item);
    const h = new T.CylinderGeometry(radius * 0.5, radius * 0.5, width * 0.86, 24);
    h.rotateX(Math.PI / 2);
    h.translate(cx, groundY + radius, cz);
    add(h, hub, item);
    if (brake) {
      const b = new T.CylinderGeometry(radius * 0.36, radius * 0.36, width * 0.9, 20);
      b.rotateX(Math.PI / 2);
      b.translate(cx, groundY + radius, cz + Math.sign(cz) * -0.02);
      add(b, carbon, brakesPart);
    }
  };
  const strut = (a: T.Vector3, b: T.Vector3, r: number, item: Part, m: T.Material = chrome) => {
    const len = a.distanceTo(b);
    const g = new T.CylinderGeometry(r, r, len, 14);
    g.translate(0, len / 2, 0);
    const o = add(g, m, item);
    o.position.copy(b);
    o.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), a.clone().sub(b).normalize());
    return o;
  };
  {
    const d = G.noseD;
    const x = X(d);
    const r = G.noseTire / 2;
    const axle = new T.Vector3(x, groundY + r, 0);
    const topP = new T.Vector3(x + 0.25, contour(d).bot + 0.15, 0);
    strut(topP, axle.clone().add(new T.Vector3(0, 0.25, 0)), 0.09, noseGear);
    wheel(x, 0.26, r, 0.24, noseGear, false);
    wheel(x, -0.26, r, 0.24, noseGear, false);
    const ax = new T.CylinderGeometry(0.05, 0.05, 0.6, 10);
    ax.rotateX(Math.PI / 2);
    ax.translate(x, groundY + r, 0);
    add(ax, chrome, noseGear);
  }
  for (const right of [true, false]) {
    const s = right ? 1 : -1;
    const d = G.mainD;
    const x = X(d);
    const r = G.mainTire / 2;
    const zr = G.track / 2;
    const wingLow = wingY(zr) - tcAt(zr) * chord(zr) * 0.45;
    const topP = new T.Vector3(x, wingLow, s * zr);
    const axle = new T.Vector3(x, groundY + r, s * zr);
    strut(topP, axle.clone().add(new T.Vector3(0, 0.1, 0)), 0.13, gearPart);
    // side brace towards the fuselage
    strut(new T.Vector3(x - 0.2, wingLow + 0.05, s * (zr - 1.3)), new T.Vector3(x, (wingLow + axle.y) / 2 + 0.4, s * zr), 0.06, gearPart, coreMetal);
    const ax = new T.CylinderGeometry(0.07, 0.07, 1.05, 10);
    ax.rotateX(Math.PI / 2);
    ax.translate(x, groundY + r, s * zr);
    add(ax, chrome, gearPart);
    wheel(x, s * (zr + 0.46), r, 0.4, gearPart, true);
    wheel(x, s * (zr - 0.46), r, 0.4, gearPart, true);
  }

  // ------------------------------------------------------------ internal systems (cutaway)
  const avionics = part("avionics_bay", "Avionics compartment", "Systems",
    "Main avionics (E/E) bay below the flight deck: flight management, air data / inertial reference and display computers.", "avionics", true);
  for (let i = 0; i < 4; i++) {
    const g = new T.BoxGeometry(0.42, 0.62, 0.55);
    g.translate(X(P.nose.len * 0.62 + i * 0.5), -H * 0.55, (i % 2 ? 0.35 : -0.35));
    add(g, equipment, avionics);
  }
  const hydr = part("hydraulics", "Hydraulic systems", "Systems",
    `${P.hydraulics.join(", ")}: reservoirs, engine-driven and electric pumps feeding flight controls, gear and brakes.`, "hydraulics", true);
  P.hydraulics.forEach((_, i) => {
    const g = new T.CylinderGeometry(0.2, 0.2, 0.55, 18);
    g.rotateZ(Math.PI / 2);
    g.translate(X(Wg.apexD + Wg.rootChord + 0.4 + i * 0.7), -H * 0.6, (i - 1) * 0.6);
    add(g, equipment, hydr);
  });
  void hydr;

  // Ground reference, so the scene can sit the aircraft on a floor.
  root.userData.groundY = groundY;
  root.userData.length = L;
  return { root, parts, meshes, shells, internals, fans, profile };
}
