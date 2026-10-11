import * as T from "three";

export type Part = {
  id: string;
  label: string;
  group: string;
  component?: string;
  description: string;
  internal?: boolean;
};
export const profiles = {
  a320: {
    label: "Airbus A320-200",
    length: 37.57,
    span: 35.8,
    diameter: 3.95,
    windows: 31,
    nose: 2.2,
  },
  b738: {
    label: "Boeing 737-800",
    length: 39.5,
    span: 35.8,
    diameter: 3.76,
    windows: 39,
    nose: 2.7,
  },
};
export function profileFor(type: string) {
  return /B738|737/i.test(type)
    ? profiles.b738
    : /A320/i.test(type)
      ? profiles.a320
      : null;
}
export function buildAircraft(type: string) {
  const profile = profileFor(type);
  const p = profile ?? profiles.a320;
  const boeing = p === profiles.b738;
  const root = new T.Group();
  const parts: Part[] = [];
  const meshes: T.Mesh[] = [];
  const shells: T.Mesh[] = [];
  const internals: T.Mesh[] = [];
  const fans: T.Group[] = [];
  const L = p.length / 2.7,
    R = p.diameter / 5.4,
    span = p.span / 5.4;
  const paint = new T.MeshStandardMaterial({
    color: 0xf0eee8,
    roughness: 0.43,
    metalness: 0.12,
  });
  const navy = new T.MeshStandardMaterial({
    color: 0x344653,
    roughness: 0.55,
    metalness: 0.12,
  });
  const glass = new T.MeshStandardMaterial({
    color: 0x28363d,
    roughness: 0.18,
    metalness: 0.45,
  });
  const alloy = new T.MeshStandardMaterial({
    color: 0x8b9091,
    roughness: 0.35,
    metalness: 0.72,
  });
  const rubber = new T.MeshStandardMaterial({
    color: 0x292a28,
    roughness: 0.88,
  });
  const warm = new T.MeshStandardMaterial({
    color: 0x9f8b71,
    roughness: 0.6,
    metalness: 0.55,
  });
  function part(
    id: string,
    label: string,
    group: string,
    description: string,
    component?: string,
    internal = false,
  ) {
    const item = { id, label, group, description, component, internal };
    parts.push(item);
    return item;
  }
  function mesh(
    g: T.BufferGeometry,
    m: T.Material,
    pos: [number, number, number],
    item: Part,
    parent: T.Group = root,
    shell = false,
  ) {
    const o = new T.Mesh(g, m.clone());
    o.position.set(...pos);
    o.castShadow = true;
    o.receiveShadow = true;
    o.userData.part = item.id;
    parent.add(o);
    meshes.push(o);
    if (shell) shells.push(o);
    if (item.internal) {
      o.visible = false;
      internals.push(o);
    }
    return o;
  }
  const body = part(
    "fuselage",
    "Fuselage & pressure shell",
    "Airframe",
    "Smooth pressure shell, cabin windows and passenger doors. Geometry reference only.",
  );
  // Dense sampled radius profile, with a round Airbus nose and longer Boeing nose.
  const knots = [
    [-L / 2, 0],
    [-L / 2 + 0.18, 0.2],
    [-L / 2 + p.nose / 2.7, 0.82],
    [-L / 2 + 1.65, 1],
    [L / 2 - 3.4, 1],
    [L / 2 - 2, 0.72],
    [L / 2 - 0.5, 0.22],
    [L / 2, 0],
  ];
  function radius(z: number) {
    let i = 0;
    while (i < knots.length - 2 && z > knots[i + 1][0]) i++;
    const t = T.MathUtils.clamp(
      (z - knots[i][0]) / (knots[i + 1][0] - knots[i][0]),
      0,
      1,
    );
    return (
      R * T.MathUtils.lerp(knots[i][1], knots[i + 1][1], t * t * (3 - 2 * t))
    );
  }
  const points = Array.from({ length: 161 }, (_, i) => {
    const z = -L / 2 + (i * L) / 160;
    return new T.Vector2(radius(z), z);
  });
  const fuselage = mesh(
    new T.LatheGeometry(points, 96),
    paint,
    [0, 0.25, 0],
    body,
    root,
    true,
  );
  fuselage.rotation.x = Math.PI / 2;
  const radome = part(
    "radome",
    "Nose radome",
    "Avionics",
    "Weather radar fairing; selection shows the shared avionics telemetry.",
    "avionics",
  );
  const ring = mesh(
    new T.TorusGeometry(radius(-L / 2 + 0.8), 0.009, 6, 64),
    alloy,
    [0, 0.25, -L / 2 + 0.8],
    radome,
  );
  ring.scale.y = 1;
  const cockpit = part(
    "cockpit",
    "Flight deck glazing",
    "Avionics",
    "Six individual windscreen panes following the forward fuselage contour.",
    "avionics",
  );
  // Surface patches sit on the pressure shell, rather than a floating cockpit bubble.
  function patch(
    z0: number,
    z1: number,
    a0: number,
    a1: number,
    item: Part,
    mat: T.Material,
  ) {
    const v: number[] = [],
      idx: number[] = [];
    const n = 8;
    for (let i = 0; i <= n; i++)
      for (let j = 0; j <= n; j++) {
        const z = T.MathUtils.lerp(z0, z1, i / n),
          a = T.MathUtils.lerp(a0, a1, j / n),
          r = radius(z) + 0.007;
        v.push(Math.sin(a) * r, 0.25 + Math.cos(a) * r, z);
      }
    for (let i = 0; i < n; i++)
      for (let j = 0; j < n; j++) {
        const k = i * (n + 1) + j;
        idx.push(k, k + n + 1, k + 1, k + 1, k + n + 1, k + n + 2);
      }
    const g = new T.BufferGeometry();
    g.setAttribute("position", new T.Float32BufferAttribute(v, 3));
    g.setIndex(idx);
    g.computeVertexNormals();
    const o = mesh(g, mat, [0, 0, 0], item);
    (o.material as T.MeshStandardMaterial).side = T.DoubleSide;
  }
  for (const s of [-1, 1]) {
    for (let i = 0; i < 3; i++)
      patch(
        -L / 2 + 1.02 + i * 0.28,
        -L / 2 + 1.26 + i * 0.28,
        s * (0.48 + i * 0.24),
        s * (0.69 + i * 0.24),
        cockpit,
        glass,
      );
    for (let i = 0; i < p.windows; i++) {
      const z = -L / 2 + 2.15 + (i * (L - 4.7)) / (p.windows - 1);
      patch(z - 0.055, z + 0.055, s * 1.16, s * 1.35, body, glass);
    }
    for (const z of [-L / 2 + 2.0, L / 2 - 2.8]) {
      const door = part(
        `door-${s}-${z}`,
        s < 0 ? "Port passenger door" : "Starboard passenger door",
        "Airframe",
        "Passenger door outline. Geometry reference only.",
      );
      patch(z - 0.13, z + 0.13, s * 0.85, s * 1.9, door, alloy);
      patch(z - 0.115, z + 0.115, s * 0.86, s * 1.89, door, paint);
    }
    patch(-L / 2 + 2.5, L / 2 - 2.4, s * 1.48, s * 1.52, body, navy);
  }
  // Swept surfaces use a closed airfoil cross section with thin trailing edges.
  function foil(
    sections: number[][],
    item: Part,
    mat: T.Material,
    vertical = false,
  ) {
    const v: number[] = [],
      idx: number[] = [];
    const n = 32;
    sections.forEach(([x, y, z, chord, thickness]) => {
      for (let k = 0; k < n; k++) {
        const a = (k / (n - 1)) * Math.PI * 2,
          u = (1 - Math.cos(a)) / 2;
        const t =
          5 *
          thickness *
          (0.2969 * Math.sqrt(u) -
            0.126 * u -
            0.3516 * u * u +
            0.2843 * u * u * u -
            0.1036 * u * u * u * u) *
          Math.sign(Math.sin(a));
        if (vertical) v.push(t * chord + x, y, z + u * chord);
        else v.push(x, y + t * chord, z + u * chord);
      }
    });
    for (let s = 0; s < sections.length - 1; s++)
      for (let k = 0; k < n; k++) {
        const a = s * n + k,
          b = s * n + ((k + 1) % n);
        idx.push(a, b, a + n, b, b + n, a + n);
      }
    for (const start of [0, (sections.length - 1) * n])
      for (let k = 1; k < n - 1; k++) idx.push(start, start + k, start + k + 1);
    const g = new T.BufferGeometry();
    g.setAttribute("position", new T.Float32BufferAttribute(v, 3));
    g.setIndex(idx);
    g.computeVertexNormals();
    const o = mesh(g, mat, [0, 0, 0], item);
    (o.material as T.MeshStandardMaterial).side = T.DoubleSide;
    return o;
  }
  for (const s of [-1, 1]) {
    const side = s < 0 ? "Port" : "Starboard";
    const wing = part(
      `wing-${s}`,
      `${side} wing & leading edge`,
      "Airframe",
      "Swept wing with tapered airfoil, dihedral and a shaped wingtip. Geometry reference only.",
    );
    foil(
      [
        [s * 0.48, 0.08, -1.7, 4.0, 0.12],
        [s * 2.1, 0.14, -0.8, 2.8, 0.1],
        [s * (span - 0.35), 0.48, 2.2, 0.65, 0.08],
      ],
      wing,
      paint,
    );
    const tip = part(
      `tip-${s}`,
      `${side} ${boeing ? "blended winglet" : "sharklet"}`,
      "Airframe",
      "Type-specific wingtip reference; installed configuration is not supplied by the simulator.",
    );
    foil(
      [
        [s * (span - 0.35), 0.48, 2.2, 0.65, 0.07],
        [s * (span - 0.12), 0.85, 2.45, 0.52, 0.07],
        [s * span, boeing ? 1.65 : 1.45, 2.8, 0.3, 0.06],
      ],
      tip,
      navy,
    );
    const flap = part(
      `flap-${s}`,
      `${side} flaps & aileron`,
      "Flight controls",
      "Trailing-edge surfaces; hydraulic health is shared with the parent system.",
      "hydraulics",
    );
    foil(
      [
        [s * 1.1, 0.065, 1.4, 0.48, 0.09],
        [s * 3.7, 0.29, 2.5, 0.4, 0.08],
        [s * 5.8, 0.44, 2.83, 0.23, 0.07],
      ],
      flap,
      alloy,
    );
    for (let i = 0; i < 3; i++) {
      const fair = mesh(
        new T.SphereGeometry(0.11, 16, 12),
        paint,
        [s * (1.8 + i * 1.2), -0.02 + i * 0.08, 1.85 + i * 0.31],
        flap,
      );
      fair.scale.set(0.7, 0.7, 4);
    }
    const stab = part(
      `stabilizer-${s}`,
      `${side} stabilizer & elevator`,
      "Flight controls",
      "Horizontal stabilizer and elevator reference.",
      "hydraulics",
    );
    foil(
      [
        [s * 0.28, 0.6, L / 2 - 2.6, 1.95, 0.09],
        [s * 2.7, 0.8, L / 2 - 0.9, 0.55, 0.08],
      ],
      stab,
      paint,
    );
    const kind = s < 0 ? "engine_1" : "engine_2";
    const nacelle = part(
      `${kind}-nacelle`,
      `${side} engine nacelle`,
      "Engines",
      "CFM56-style reference nacelle and thrust reverser; installed engine variant is not supplied. ",
      kind,
    );
    const engine = new T.Group();
    engine.position.set(s * 2.15, boeing ? -0.67 : -0.72, -0.82);
    root.add(engine);
    const ep = [
      new T.Vector2(0.49, -1.0),
      new T.Vector2(0.55, -0.95),
      new T.Vector2(0.57, -0.7),
      new T.Vector2(0.55, 0.25),
      new T.Vector2(0.4, 0.9),
      new T.Vector2(0.34, 1),
      new T.Vector2(0.3, 0.98),
      new T.Vector2(0.42, 0.2),
      new T.Vector2(0.46, -0.85),
      new T.Vector2(0.49, -1),
    ];
    const shell = mesh(
      new T.LatheGeometry(ep, 64),
      paint,
      [0, 0, 0],
      nacelle,
      engine,
      true,
    );
    shell.rotation.x = Math.PI / 2;
    if (boeing) {
      const attr = shell.geometry.getAttribute("position");
      for (let i = 0; i < attr.count; i++) attr.setX(i, attr.getX(i) * 1.06); // Flatten the lower lip in world coordinates after lathe rotation.
      for (let i = 0; i < attr.count; i++)
        attr.setZ(i, Math.min(attr.getZ(i), 0.43));
      shell.geometry.computeVertexNormals();
    }
    const reverser = part(
      `${kind}-reverser`,
      `${side} thrust reverser`,
      "Engines",
      "Aft nacelle reverser sleeve reference. Aggregate engine telemetry only.",
      kind,
    );
    const seam = mesh(
      new T.TorusGeometry(0.535, 0.008, 6, 64),
      alloy,
      [0, 0, 0.15],
      reverser,
      engine,
    );
    if (boeing) seam.scale.set(1.06, 0.9, 1);
    const intake = part(
      `${kind}-intake`,
      `${side} inlet & fan`,
      "Engines",
      "Inlet lip, fan blades and spinner. Uses the engine’s aggregate telemetry.",
      kind,
    );
    const lip = mesh(
      new T.TorusGeometry(0.49, 0.025, 10, 64),
      alloy,
      [0, 0, -1.0],
      intake,
      engine,
    );
    if (boeing) lip.scale.set(1.06, 0.9, 1);
    const fan = new T.Group();
    fan.position.z = -0.86;
    engine.add(fan);
    fans.push(fan);
    for (let i = 0; i < 24; i++) {
      const a = (i * Math.PI * 2) / 24;
      const blade = mesh(
        new T.BoxGeometry(0.042, 0.33, 0.018),
        alloy,
        [Math.sin(a) * 0.27, Math.cos(a) * 0.27, 0],
        intake,
        fan,
      );
      blade.rotation.z = -a + 0.28;
      blade.rotation.y = 0.4;
    }
    const spinner = mesh(
      new T.ConeGeometry(0.13, 0.26, 32),
      alloy,
      [0, 0, -0.91],
      intake,
      engine,
    );
    spinner.rotation.x = -Math.PI / 2;
    const exhaust = part(
      `${kind}-exhaust`,
      `${side} exhaust & core nozzle`,
      "Engines",
      "Concentric core nozzle and tail cone.",
      kind,
    );
    const nozzle = mesh(
      new T.CylinderGeometry(0.28, 0.2, 0.5, 40, 1, true),
      alloy,
      [0, 0, 1],
      exhaust,
      engine,
    );
    nozzle.rotation.x = Math.PI / 2;
    const cone = mesh(
      new T.ConeGeometry(0.14, 0.6, 32),
      warm,
      [0, 0, 1.25],
      exhaust,
      engine,
    );
    cone.rotation.x = Math.PI / 2;
    const core = part(
      `${kind}-core`,
      `${side} compressor & turbine`,
      "Engines",
      "Schematic internal stages, visible in cutaway. No separate stage health is measured.",
      kind,
      true,
    );
    for (let i = 0; i < 7; i++) {
      const stage = mesh(
        new T.CylinderGeometry(0.3 - i * 0.017, 0.3 - i * 0.017, 0.07, 32),
        i < 4 ? alloy : warm,
        [0, 0, -0.5 + i * 0.2],
        core,
        engine,
      );
      stage.rotation.x = Math.PI / 2;
    }
    mesh(
      new T.BoxGeometry(0.15, 0.58, 1.0),
      paint,
      [s * 2.15, -0.17, -0.35],
      nacelle,
    );
    const gear = part(
      `gear-${s}`,
      `${side} main gear strut & brace`,
      "Landing gear",
      "Main oleo strut, torque link and side brace.",
      "landing_gear",
    );
    mesh(
      new T.CylinderGeometry(0.055, 0.075, 0.85, 16),
      alloy,
      [s * 1.22, -0.63, 1.2],
      gear,
    );
    const brace = mesh(
      new T.CylinderGeometry(0.035, 0.035, 0.6, 12),
      alloy,
      [s * 1.08, -0.56, 1.2],
      gear,
    );
    brace.rotation.z = s * 0.42;
    const brake = part(
      `brake-${s}`,
      `${side} main wheels & brake pack`,
      "Landing gear",
      "Twin main wheels and inboard brake discs.",
      "brakes",
    );
    for (const offset of [-0.14, 0.14]) {
      const wheel = mesh(
        new T.TorusGeometry(0.19, 0.065, 16, 32),
        rubber,
        [s * 1.22 + offset, -1.06, 1.2],
        brake,
      );
      wheel.rotation.y = Math.PI / 2;
      const hub = mesh(
        new T.CylinderGeometry(0.13, 0.13, 0.075, 24),
        alloy,
        [s * 1.22 + offset, -1.06, 1.2],
        brake,
      );
      hub.rotation.z = Math.PI / 2;
    }
  }
  const tail = part(
    "fin",
    "Vertical fin & rudder",
    "Flight controls",
    "Swept vertical fin with a distinct dorsal root; rudder shares hydraulic telemetry.",
    "hydraulics",
  );
  foil(
    [
      [0, 0.65, L / 2 - 3.25, 3.05, 0.12],
      [0, 2.25, L / 2 - 1.95, 1.55, 0.09],
      [0, 3.35, L / 2 - 0.9, 0.68, 0.08],
    ],
    tail,
    navy,
    true,
  );
  const fairing = mesh(
    new T.SphereGeometry(0.65, 32, 20),
    paint,
    [0, -0.25, 0.5],
    body,
    root,
    true,
  );
  fairing.scale.set(1.25, 0.6, 3.2);
  const antennas = part(
    "antennas",
    "Navigation & communications antennas",
    "Avionics",
    "Dorsal blade antenna reference.",
    "avionics",
  );
  const aerial = mesh(
    new T.SphereGeometry(0.1, 16, 12),
    paint,
    [0, 0.99, -1.9],
    antennas,
  );
  aerial.scale.set(0.3, 1.4, 1.8);
  const noseGear = part(
    "nose-gear",
    "Nose gear & steering",
    "Landing gear",
    "Twin nose wheels and steering strut. Nose wheels have no brake-health assignment.",
    "landing_gear",
  );
  mesh(
    new T.CylinderGeometry(0.045, 0.065, 0.88, 16),
    alloy,
    [0, -0.61, -L / 2 + 2.65],
    noseGear,
  );
  for (const s of [-1, 1]) {
    const tire = mesh(
      new T.TorusGeometry(0.135, 0.045, 12, 24),
      rubber,
      [s * 0.105, -1.06, -L / 2 + 2.65],
      noseGear,
    );
    tire.rotation.y = Math.PI / 2;
  }
  const bay = part(
    "avionics-bay",
    "Forward equipment rack",
    "Avionics",
    "Schematic equipment rack in the forward bay. Aggregate avionics health only.",
    "avionics",
    true,
  );
  for (let i = 0; i < 4; i++)
    mesh(
      new T.BoxGeometry(0.48, 0.15, 0.28),
      navy,
      [0, -0.1 + i * 0.18, -L / 2 + 2.7],
      bay,
    );
  const hydraulic = part(
    "hydraulic-bay",
    "Hydraulic manifold & lines",
    "Flight controls",
    "Schematic manifold and distribution lines; positions are illustrative.",
    "hydraulics",
    true,
  );
  mesh(new T.BoxGeometry(0.5, 0.28, 0.6), warm, [0, -0.2, 0.65], hydraulic);
  for (const s of [-1, 1]) {
    const tube = mesh(
      new T.CylinderGeometry(0.018, 0.018, 3.8, 10),
      alloy,
      [s * 2.1, 0.12, 0.75],
      hydraulic,
    );
    tube.rotation.z = Math.PI / 2;
  }
  root.updateMatrixWorld(true);
  return { root, parts, meshes, shells, internals, fans, profile };
}
