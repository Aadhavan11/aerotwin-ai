import { useEffect, useRef, useState, type ReactNode } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { TwinComponent } from "../api/types";
import { STATUS_STYLE } from "../lib/format";

type Props = {
  components: TwinComponent[];
  selected: string;
  onSelect: (id: string) => void;
  fallback: ReactNode;
};

/** Offline procedural aircraft; an illustrative twin, not a manufacturer CAD model. */
export default function AircraftScene({
  components,
  selected,
  onSelect,
  fallback,
}: Props) {
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef({ components, selected, onSelect });
  latest.current = { components, selected, onSelect };
  const controller = useRef<{
    view: (mode: string) => void;
    orbit: (enabled: boolean) => void;
    cutaway: (enabled: boolean) => void;
  } | null>(null);
  const [failed, setFailed] = useState(false);
  const [orbit, setOrbit] = useState(false);
  const [cutaway, setCutaway] = useState(false);
  const [view, setView] = useState("perspective");
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      setFailed(true);
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.4;
    renderer.domElement.setAttribute(
      "aria-label",
      "Interactive three-dimensional aircraft. Drag to orbit, scroll to zoom; select components with the buttons below.",
    );
    element.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100);
    camera.position.set(15, 10, -18);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.enablePan = false;
    controls.minDistance = 14;
    controls.maxDistance = 36;
    controls.maxPolarAngle = Math.PI * 0.49;
    controls.autoRotateSpeed = 0.35;
    controls.target.set(0, 0, 0);
    scene.add(new THREE.HemisphereLight(0xbce8ff, 0x18313e, 2.4));
    const key = new THREE.DirectionalLight(0xffffff, 4);
    key.position.set(4, 10, -5);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    Object.assign(key.shadow.camera, {
      left: -12,
      right: 12,
      top: 12,
      bottom: -12,
    });
    scene.add(key);
    const rim = new THREE.DirectionalLight(0x57e2eb, 2);
    rim.position.set(-6, 2, 6);
    scene.add(rim);
    const plane = new THREE.Group();
    scene.add(plane);
    const white = new THREE.MeshPhysicalMaterial({
      color: 0xd9e4ed,
      metalness: 0.5,
      roughness: 0.3,
      clearcoat: 0.8,
    });
    const dark = new THREE.MeshStandardMaterial({
      color: 0x0d202c,
      metalness: 0.6,
      roughness: 0.28,
    });
    const metal = new THREE.MeshStandardMaterial({
      color: 0x52697b,
      metalness: 0.85,
      roughness: 0.3,
    });
    const blue = new THREE.MeshStandardMaterial({
      color: 0x153d56,
      metalness: 0.5,
      roughness: 0.3,
    });
    const clickable: THREE.Mesh[] = [];
    const accents: {
      mesh: THREE.Mesh;
      component: string;
      material: THREE.MeshStandardMaterial;
    }[] = [];
    const fans: THREE.Group[] = [];
    const add = (
      geometry: THREE.BufferGeometry,
      material: THREE.Material,
      position: [number, number, number],
      parent: THREE.Object3D = plane,
    ) => {
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(...position);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      parent.add(mesh);
      return mesh;
    };
    const mark = (mesh: THREE.Mesh, component: string) => {
      mesh.userData.component = component;
      clickable.push(mesh);
    };
    const accent = (
      geometry: THREE.BufferGeometry,
      position: [number, number, number],
      component: string,
      parent = plane,
    ) => {
      const material = new THREE.MeshStandardMaterial({
        color: 0x43dcd0,
        emissive: 0x43dcd0,
        emissiveIntensity: 0.3,
        transparent: true,
        opacity: 0.8,
      });
      const mesh = add(geometry, material, position, parent);
      accents.push({ mesh, component, material });
      mark(mesh, component);
      return mesh;
    };
    // Fuselage is revolved from a tapered profile, with metallic longitudinal surfaces.
    const profile = [
      [0, -7],
      [0.18, -6.8],
      [0.44, -6.2],
      [0.62, -5.2],
      [0.69, -3],
      [0.7, 2.8],
      [0.56, 4.4],
      [0.3, 6.2],
      [0.04, 7],
    ].map(([r, y]) => new THREE.Vector2(r, y));
    const fuselage = add(
      new THREE.LatheGeometry(profile, 64),
      white,
      [0, 0.25, 0],
    );
    fuselage.rotation.x = Math.PI / 2;
    mark(fuselage, "hydraulics");
    const cockpit = add(
      new THREE.SphereGeometry(0.54, 32, 16),
      dark,
      [0, 0.61, -5.65],
    );
    cockpit.scale.set(0.9, 0.48, 1.12);
    mark(cockpit, "avionics");
    for (const side of [-1, 1]) {
      for (let i = 0; i < 25; i++) {
        const window = add(new THREE.BoxGeometry(0.025, 0.12, 0.14), dark, [
          side * 0.675,
          0.43,
          -4.3 + i * 0.31,
        ]);
        window.rotation.z = side * 0.06;
      }
      const stripe = add(new THREE.BoxGeometry(0.02, 0.06, 8.2), blue, [
        side * 0.69,
        0.15,
        -0.3,
      ]);
      stripe.rotation.z = side * 0.03;
    }
    const surface = (
      points: [number, number][],
      y: number,
      material: THREE.Material,
    ) => {
      const shape = new THREE.Shape(
        points.map(([x, z]) => new THREE.Vector2(x, z)),
      );
      const mesh = add(
        new THREE.ExtrudeGeometry(shape, {
          depth: 0.1,
          bevelEnabled: true,
          bevelThickness: 0.035,
          bevelSize: 0.04,
          bevelSegments: 2,
          steps: 1,
        }),
        material,
        [0, y, 0],
      );
      mesh.rotation.x = Math.PI / 2;
      return mesh;
    };
    for (const side of [-1, 1]) {
      const wing = surface(
        [
          [side * 0.35, -1.8],
          [side * 7.6, 2.2],
          [side * 7.8, 3.25],
          [side * 5.2, 3.4],
          [side * 0.5, 1.8],
        ],
        0.1,
        white,
      );
      mark(wing, "hydraulics");
      surface(
        [
          [side * 0.5, 4.5],
          [side * 3.4, 6.2],
          [side * 3.5, 6.8],
          [side * 0.2, 6.2],
        ],
        0.48,
        white,
      );
      const winglet = add(new THREE.BoxGeometry(0.12, 0.9, 1.1), blue, [
        side * 7.65,
        0.45,
        2.75,
      ]);
      winglet.rotation.z = side * 0.25;
      const wingLight = add(
        new THREE.SphereGeometry(0.065, 10, 10),
        new THREE.MeshBasicMaterial({ color: side < 0 ? 0xfb7185 : 0x5eead4 }),
        [side * 7.7, 0.55, 2.4],
      );
      wingLight.name = "navigation-light";
      const engine = new THREE.Group();
      engine.position.set(side * 2.65, -0.8, -0.25);
      plane.add(engine);
      const kind = side < 0 ? "engine_1" : "engine_2";
      const shell = add(
        new THREE.CylinderGeometry(0.57, 0.48, 2.25, 48, 1, true),
        white,
        [0, 0, 0],
        engine,
      );
      shell.rotation.x = Math.PI / 2;
      mark(shell, kind);
      const interior = add(
        new THREE.CylinderGeometry(0.46, 0.34, 2.15, 48, 1, true),
        dark,
        [0, 0, 0],
        engine,
      );
      interior.rotation.x = Math.PI / 2;
      mark(interior, kind);
      add(
        new THREE.TorusGeometry(0.515, 0.045, 12, 48),
        metal,
        [0, 0, -1.13],
        engine,
      );
      accent(
        new THREE.TorusGeometry(0.585, 0.025, 12, 48),
        [0, 0, -1.1],
        kind,
        engine,
      );
      add(
        new THREE.BoxGeometry(0.14, 0.65, 1.15),
        white,
        [0, 0.5, 0.3],
        engine,
      );
      const fan = new THREE.Group();
      fan.position.z = -0.88;
      engine.add(fan);
      fans.push(fan);
      for (let i = 0; i < 16; i++) {
        const blade = add(
          new THREE.BoxGeometry(0.06, 0.36, 0.035),
          metal,
          [0, 0, 0],
          fan,
        );
        const angle = (i / 16) * Math.PI * 2;
        blade.position.set(Math.sin(angle) * 0.24, Math.cos(angle) * 0.24, 0);
        blade.rotation.z = -angle + 0.28;
        mark(blade, kind);
      }
      const hub = add(
        new THREE.ConeGeometry(0.12, 0.3, 24),
        metal,
        [0, 0, -1],
        engine,
      );
      hub.rotation.x = -Math.PI / 2;
      mark(hub, kind);
      const strut = add(
        new THREE.CylinderGeometry(0.07, 0.07, 0.75, 12),
        metal,
        [side * 0.7, -0.75, 1.2],
      );
      mark(strut, "landing_gear");
      for (const wheelZ of [1.0, 1.38]) {
        const wheel = add(
          new THREE.CylinderGeometry(0.22, 0.22, 0.22, 20),
          dark,
          [side * 0.78, -1.12, wheelZ],
        );
        wheel.rotation.z = Math.PI / 2;
        mark(wheel, "brakes");
      }
    }
    const tailShape = new THREE.Shape([
      new THREE.Vector2(0, 0),
      new THREE.Vector2(-1.4, 2.6),
      new THREE.Vector2(-2.2, 2.6),
      new THREE.Vector2(-3, 0),
    ]);
    const fin = add(
      new THREE.ExtrudeGeometry(tailShape, { depth: 0.1, bevelEnabled: false }),
      blue,
      [-0.05, 0.65, 4.4],
    );
    fin.rotation.y = Math.PI / 2;
    accent(
      new THREE.SphereGeometry(0.13, 16, 16),
      [0, 0.84, -5.15],
      "avionics",
    );
    const gear = add(
      new THREE.CylinderGeometry(0.055, 0.055, 0.8, 10),
      metal,
      [0, -0.62, -4.2],
    );
    mark(gear, "landing_gear");
    const noseWheel = add(
      new THREE.CylinderGeometry(0.15, 0.15, 0.2, 16),
      dark,
      [0, -1.05, -4.2],
    );
    noseWheel.rotation.z = Math.PI / 2;
    mark(noseWheel, "brakes");
    const ground = add(
      new THREE.PlaneGeometry(45, 45),
      new THREE.ShadowMaterial({ opacity: 0.23 }),
      [0, -1.37, 0],
      scene,
    );
    ground.rotation.x = -Math.PI / 2;
    accent(new THREE.SphereGeometry(0.11, 16, 16), [0, 1.0, 0.8], "hydraulics");
    accent(
      new THREE.TorusGeometry(0.16, 0.024, 8, 24),
      [0, -0.8, -4.2],
      "landing_gear",
    ).rotation.y = Math.PI / 2;
    accent(
      new THREE.TorusGeometry(0.25, 0.025, 8, 24),
      [0.9, -1.12, 1],
      "brakes",
    ).rotation.y = Math.PI / 2;
    const wire = new THREE.Group();
    plane.updateMatrixWorld(true);
    plane.traverse((o) => {
      if (o instanceof THREE.Mesh && o.material === white) {
        const outline = new THREE.LineSegments(
          new THREE.EdgesGeometry(o.geometry, 25),
          new THREE.LineBasicMaterial({
            color: 0x59aabe,
            transparent: true,
            opacity: 0.3,
          }),
        );
        outline.matrix.copy(o.matrixWorld);
        outline.matrixAutoUpdate = false;
        wire.add(outline);
      }
    });
    wire.visible = false;
    plane.add(wire);
    const grid = new THREE.GridHelper(36, 36, 0x214d5d, 0x102a36);
    grid.position.y = -1.4;
    scene.add(grid);
    const ring = add(
      new THREE.TorusGeometry(10, 0.015, 4, 96),
      new THREE.MeshBasicMaterial({ color: 0x275665 }),
      [0, -1.39, 0],
      scene,
    );
    ring.rotation.x = Math.PI / 2;
    const raycaster = new THREE.Raycaster();
    let down: [number, number] = [0, 0];
    const pointerDown = (e: PointerEvent) => {
      down = [e.clientX, e.clientY];
    };
    const pointerUp = (e: PointerEvent) => {
      if (Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
      const rect = renderer.domElement.getBoundingClientRect();
      raycaster.setFromCamera(
        new THREE.Vector2(
          ((e.clientX - rect.left) / rect.width) * 2 - 1,
          (-(e.clientY - rect.top) / rect.height) * 2 + 1,
        ),
        camera,
      );
      const hit = raycaster.intersectObjects(clickable)[0];
      if (hit) {
        const target = latest.current.components.find((c) =>
          c.component_id.endsWith("/" + hit.object.userData.component),
        );
        if (target) latest.current.onSelect(target.component_id);
      }
    };
    renderer.domElement.addEventListener("pointerdown", pointerDown);
    renderer.domElement.addEventListener("pointerup", pointerUp);
    const resize = new ResizeObserver(() => {
      const w = element.clientWidth,
        h = element.clientHeight;
      if (w && h) {
        renderer.setSize(w, h);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      }
    });
    resize.observe(element);
    controller.current = {
      view(mode) {
        camera.position.set(
          ...((mode === "top"
            ? [0, 25, 0.01]
            : mode === "side"
              ? [24, 3, 0]
              : [15, 10, -18]) as [number, number, number]),
        );
        controls.update();
      },
      orbit(enabled) {
        controls.autoRotate = enabled;
      },
      cutaway(enabled) {
        white.transparent = enabled;
        white.opacity = enabled ? 0.23 : 1;
        white.depthWrite = !enabled;
        wire.visible = enabled;
      },
    };
    const reduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    let frame = 0,
      last = 0;
    const animate = (time: number) => {
      frame = requestAnimationFrame(animate);
      if (document.hidden || time - last < 32) return;
      last = time;
      for (const fan of fans) if (!reduced) fan.rotation.z = time * 0.0015;
      for (const item of accents) {
        const c = latest.current.components.find((c) =>
          c.component_id.endsWith("/" + item.component),
        );
        const color = STATUS_STYLE[c?.status ?? "ok"]?.dot ?? "#38bdf8";
        item.material.color.set(color);
        item.material.emissive.set(color);
        item.material.emissiveIntensity =
          c?.component_id === latest.current.selected ? 0.6 : 0.15;
      }
      controls.update();
      renderer.render(scene, camera);
    };
    frame = requestAnimationFrame(animate);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      controls.dispose();
      renderer.dispose();
      renderer.domElement.removeEventListener("pointerdown", pointerDown);
      renderer.domElement.removeEventListener("pointerup", pointerUp);
      scene.traverse((o) => {
        if (o instanceof THREE.Mesh || o instanceof THREE.LineSegments) {
          o.geometry.dispose();
          const mats = Array.isArray(o.material) ? o.material : [o.material];
          mats.forEach((m) => m.dispose());
        }
      });
      element.replaceChildren();
      controller.current = null;
    };
  }, []);
  if (failed)
    return (
      <div className="scene-fallback">
        {fallback}
        <p>
          3D rendering unavailable on this device · component selection remains
          available
        </p>
      </div>
    );
  return (
    <div className="aircraft-scene">
      <div ref={host} className="scene-canvas" />
      <div className="scene-readout">
        <span className="live-dot" /> DIGITAL TWIN / GEOMETRY VIEW
        <span className="scene-readout-secondary">
          ILLUSTRATIVE AIRFRAME · SIMULATED
        </span>
      </div>
      <div className="scene-scale">
        <span>01 / AIRFRAME</span>
        <span>ORBIT • PAN LOCKED</span>
      </div>
      <div className="scene-tools">
        <button
          aria-pressed={cutaway}
          onClick={() => {
            setCutaway(!cutaway);
            controller.current?.cutaway(!cutaway);
          }}
        >
          Systems {cutaway ? "on" : "off"}
        </button>
        {["perspective", "top", "side"].map((mode) => (
          <button
            key={mode}
            aria-pressed={view === mode}
            onClick={() => {
              setView(mode);
              controller.current?.view(mode);
            }}
          >
            {mode}
          </button>
        ))}
        <button
          aria-pressed={orbit}
          onClick={() => {
            setOrbit(!orbit);
            controller.current?.orbit(!orbit);
          }}
        >
          Auto orbit {orbit ? "on" : "off"}
        </button>
      </div>
      <div className="scene-hint">
        DRAG TO ORBIT <span>/</span> SCROLL TO ZOOM <span>/</span> CLICK TO
        INSPECT
      </div>
    </div>
  );
}
