import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import type { TwinComponent } from "../api/types";
import { buildAircraft, profileFor, type Part } from "./aircraftModels";

type Props = {
  aircraftType: string;
  components: TwinComponent[];
  selected: string;
  onSelect: (id: string) => void;
  fallback: ReactNode;
};

const GROUPS = ["Engines", "Landing gear", "Systems", "Flight controls", "Airframe"] as const;
const VIEWS: Record<string, { pos: [number, number, number]; target: [number, number, number]; label: string }> = {
  three: { pos: [40, 17, -52], target: [-1.5, -0.8, 0], label: "3/4" },
  side: { pos: [0, 1.5, -66], target: [0, 1, 0], label: "Side" },
  top: { pos: [0.01, 92, 0], target: [0, 0, 0], label: "Top" },
  front: { pos: [66, 1.5, 0], target: [0, 0.5, 0], label: "Front" },
  engine: { pos: [0, 0, 0], target: [0, 0, 0], label: "Engine" },
  nose: { pos: [0, 0, 0], target: [0, 0, 0], label: "Nose" },
};
// Close-ups frame a part instead of using a fixed camera.
const CLOSE: Record<string, { part: string; dir: [number, number, number]; dist: number }> = {
  engine: { part: "engine_1_nacelle", dir: [1, 0.22, -0.95], dist: 8.5 },
  nose: { part: "cockpit", dir: [0.75, 0.3, -1], dist: 9 },
};

const tint = { alert: new THREE.Color(0xc0392b), watch: new THREE.Color(0xc27c0e), select: new THREE.Color(0x2f5d8a) };

export default function AircraftScene({ aircraftType, components, selected, onSelect, fallback }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef({ components, selected, part: "" });
  const [failed, setFailed] = useState(false);
  const [cutaway, setCutaway] = useState(false);
  const [view, setView] = useState("three");
  const [partId, setPartId] = useState("");
  const [parts, setParts] = useState<Part[]>([]);
  const [group, setGroup] = useState<(typeof GROUPS)[number]>("Engines");
  latest.current = { components, selected, part: partId };
  const controller = useRef<{ view: (v: string) => void; cutaway: (v: boolean) => void; focus: (p: Part) => void } | null>(null);
  const profile = useMemo(() => profileFor(aircraftType), [aircraftType]);

  const statusOf = (component?: string) =>
    component ? components.find((c) => c.component_id.endsWith("/" + component))?.status : undefined;

  function selectPart(p: Part) {
    setPartId(p.id);
    setGroup(p.group as (typeof GROUPS)[number]);
    if (p.component) {
      const c = components.find((x) => x.component_id.endsWith("/" + p.component));
      if (c) onSelect(c.component_id);
    }
    if (p.internal && !cutaway) {
      setCutaway(true);
      controller.current?.cutaway(true);
    }
  }
  const selectRef = useRef(selectPart);
  selectRef.current = selectPart;

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    setFailed(false);
    setPartId("");
    setCutaway(false);
    const initial = new URLSearchParams(window.location.search).get("view") ?? "three";
    setView(VIEWS[initial] ? initial : "three");
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
    renderer.toneMapping = THREE.NeutralToneMapping;
    renderer.toneMappingExposure = 1.05;
    element.replaceChildren(renderer.domElement);

    const scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(renderer);
    const env = pmrem.fromScene(new RoomEnvironment(), 0.035).texture;
    scene.environment = env;
    scene.environmentIntensity = 0.85;

    const camera = new THREE.PerspectiveCamera(30, 1, 0.5, 400);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.enablePan = false;
    controls.minDistance = 4;
    controls.maxDistance = 120;
    controls.maxPolarAngle = Math.PI * 0.495;

    const key = new THREE.DirectionalLight(0xffffff, 2.3);
    key.position.set(18, 42, -22);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    Object.assign(key.shadow.camera, { left: -26, right: 26, top: 26, bottom: -26, near: 1, far: 120 });
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = 0.02;
    key.shadow.radius = 4;
    scene.add(key);
    scene.add(new THREE.HemisphereLight(0xffffff, 0xe9e7e2, 0.9));

    const model = buildAircraft(aircraftType);
    scene.add(model.root);
    setParts(model.parts);
    const groundY = model.root.userData.groundY as number;
    const ground = new THREE.Mesh(new THREE.CircleGeometry(80, 64), new THREE.ShadowMaterial({ opacity: 0.13 }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = groundY;
    ground.receiveShadow = true;
    scene.add(ground);

    const place = (name: string) => {
      const close = CLOSE[name];
      if (close) {
        model.root.updateMatrixWorld(true);
        const box = new THREE.Box3();
        model.meshes.filter((o) => o.userData.part === close.part).forEach((o) => box.expandByObject(o));
        if (!box.isEmpty()) {
          const c = box.getCenter(new THREE.Vector3());
          controls.target.copy(c);
          camera.position.copy(c.clone().add(new THREE.Vector3(...close.dir).normalize().multiplyScalar(close.dist)));
          controls.update();
          return;
        }
      }
      const v = VIEWS[name] ?? VIEWS.three;
      camera.position.set(...v.pos);
      controls.target.set(...v.target);
      controls.update();
    };
    place(VIEWS[initial] ? initial : "three");

    const cut = (enabled: boolean) => {
      for (const o of model.shells) {
        const m = o.material as THREE.MeshStandardMaterial;
        m.transparent = enabled;
        m.opacity = enabled ? 0.16 : 1;
        m.depthWrite = !enabled;
      }
      for (const o of model.internals) o.visible = enabled;
    };
    controller.current = {
      view: place,
      cutaway: cut,
      focus(p) {
        const hits = model.meshes.filter((o) => o.userData.part === p.id && o.visible !== false);
        if (!hits.length) return;
        model.root.updateMatrixWorld(true);
        const box = new THREE.Box3();
        hits.forEach((o) => box.expandByObject(o));
        const c = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3()).length();
        const dir = camera.position.clone().sub(controls.target).normalize();
        controls.target.copy(c);
        camera.position.copy(c.clone().add(dir.multiplyScalar(Math.max(14, size * 2.4))));
        controls.update();
      },
    };

    if (import.meta.env.DEV) {
      // Dev-only hooks for visual checks: switch view and grab the current frame.
      const w = window as unknown as Record<string, unknown>;
      w.__twinView = (name: string) => place(name);
      w.__twinShot = (type = "image/jpeg") => {
        renderer.render(scene, camera);
        return renderer.domElement.toDataURL(type, 0.9);
      };
      w.__twinCut = (on: boolean) => cut(on);
    }
    const ray = new THREE.Raycaster();
    let down = [0, 0];
    const pointerDown = (e: PointerEvent) => {
      down = [e.clientX, e.clientY];
    };
    const pointerUp = (e: PointerEvent) => {
      if (Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
      const r = renderer.domElement.getBoundingClientRect();
      ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, (-(e.clientY - r.top) / r.height) * 2 + 1), camera);
      const targets = model.meshes.filter(
        (o) => o.visible && !(model.shells.includes(o) && (o.material as THREE.MeshStandardMaterial).opacity < 0.5),
      );
      const hit = ray.intersectObjects(targets, false)[0];
      const part = model.parts.find((p) => p.id === hit?.object.userData.part);
      if (part) selectRef.current(part);
    };
    renderer.domElement.addEventListener("pointerdown", pointerDown);
    renderer.domElement.addEventListener("pointerup", pointerUp);
    const resize = new ResizeObserver(() => {
      const w = element.clientWidth;
      const h = element.clientHeight;
      if (w && h) {
        renderer.setSize(w, h);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      }
    });
    resize.observe(element);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const partById = new Map(model.parts.map((p) => [p.id, p]));
    let frame = 0;
    let last = 0;
    const animate = (t: number) => {
      frame = requestAnimationFrame(animate);
      // rAF already pauses in background tabs; do not also test document.hidden, which embedded views report as true.
      if (t - last < 32) return;
      last = t;
      if (!reduced) for (const fan of model.fans) fan.rotation.x = -t * 0.0011;
      const { selected: sel, part: activeId, components: comps } = latest.current;
      const active = partById.get(activeId);
      const exact = active && (!active.component || sel.endsWith("/" + active.component));
      for (const o of model.meshes) {
        const p = partById.get(o.userData.part);
        const isSel = exact ? p?.id === active?.id : !!p?.component && sel.endsWith("/" + p.component);
        const status = p?.component ? comps.find((c) => c.component_id.endsWith("/" + p.component))?.status : undefined;
        const m = o.material as THREE.MeshStandardMaterial;
        if (isSel) {
          m.emissive.copy(tint.select);
          m.emissiveIntensity = 0.28;
        } else if (status === "alert" || status === "watch") {
          m.emissive.copy(tint[status]);
          m.emissiveIntensity = status === "alert" ? 0.32 + (reduced ? 0 : 0.12 * Math.sin(t / 380)) : 0.22;
        } else {
          m.emissiveIntensity = 0;
        }
      }
      controls.update();
      renderer.render(scene, camera);
    };
    frame = requestAnimationFrame(animate);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", pointerDown);
      renderer.domElement.removeEventListener("pointerup", pointerUp);
      const geometries = new Set<THREE.BufferGeometry>();
      const materials = new Set<THREE.Material>();
      scene.traverse((o) => {
        if (o instanceof THREE.Mesh || o instanceof THREE.Line) {
          geometries.add(o.geometry);
          (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => materials.add(m));
        }
      });
      geometries.forEach((g) => g.dispose());
      materials.forEach((m) => m.dispose());
      env.dispose();
      pmrem.dispose();
      renderer.dispose();
      element.replaceChildren();
      controller.current = null;
    };
  }, [aircraftType]);

  const chosen = parts.find((p) => p.id === partId);
  const visibleParts = parts.filter((p) => p.group === group);
  const linked = (p: Part) => (p.component ? components.find((c) => c.component_id.endsWith("/" + p.component)) : undefined);

  return (
    <div className="study">
      {failed ? (
        <div className="study-fallback">
          {fallback}
          <p>3D is not available on this device; the plan view and the parts list still work.</p>
        </div>
      ) : (
        <div className="study-stage">
          <div ref={host} className="study-canvas" />
          <div className="study-title">
            <strong>{profile?.label ?? "Narrow-body (generic)"}</strong>
            <span>
              {profile
                ? `${profile.engine} engines · ${profile.length} m long · ${profile.span} m span`
                : "Aircraft type not recognised; generic geometry"}
            </span>
          </div>
          <div className="study-views" role="group" aria-label="Camera">
            {Object.entries(VIEWS).map(([k, v]) => (
              <button
                key={k}
                aria-pressed={view === k}
                onClick={() => {
                  setView(k);
                  controller.current?.view(k);
                }}
              >
                {v.label}
              </button>
            ))}
            <span className="study-sep" />
            <button
              aria-pressed={cutaway}
              onClick={() => {
                setCutaway(!cutaway);
                controller.current?.cutaway(!cutaway);
              }}
            >
              Cutaway
            </button>
          </div>
          <p className="study-hint">Drag to turn, scroll to zoom, click a part.</p>
        </div>
      )}

      <div className="parts">
        <div className="parts-tabs" role="tablist" aria-label="Systems">
          {GROUPS.map((g) => (
            <button key={g} role="tab" aria-selected={group === g} onClick={() => setGroup(g)}>
              {g}
            </button>
          ))}
        </div>
        <ul className="parts-list">
          {visibleParts.map((p) => {
            const c = linked(p);
            const st = statusOf(p.component);
            return (
              <li key={p.id}>
                <button
                  aria-pressed={chosen?.id === p.id}
                  onClick={() => {
                    selectPart(p);
                    controller.current?.focus(p);
                  }}
                >
                  <span className="parts-name">{p.label}</span>
                  {c ? (
                    <span className={`parts-health s-${st}`}>{Math.round(c.health * 100)}%</span>
                  ) : (
                    <span className="parts-health muted">{p.internal ? "cutaway" : "structure"}</span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
        <div className="parts-note">
          {chosen ? (
            <>
              <strong>{chosen.label}</strong>
              <p>{chosen.description}</p>
              <p className="muted">
                {chosen.component
                  ? `Health comes from the twin's ${chosen.component.replaceAll("_", " ")} component; individual sub-parts are not measured separately.`
                  : "Structure only; no telemetry is assigned to this part."}
              </p>
            </>
          ) : (
            <p className="muted">Select a part on the model or in the list. Monitored parts show the health of their twin component.</p>
          )}
        </div>
      </div>
    </div>
  );
}
