import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { TwinComponent } from "../api/types";
import { buildAircraft, profileFor, type Part } from "./aircraftModels";

type Props = {
  aircraftType: string;
  components: TwinComponent[];
  selected: string;
  onSelect: (id: string) => void;
  fallback: ReactNode;
};
export default function AircraftScene({
  aircraftType,
  components,
  selected,
  onSelect,
  fallback,
}: Props) {
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef({ components, selected, onSelect, part: "" });
  const [failed, setFailed] = useState(false),
    [cutaway, setCutaway] = useState(false),
    [orbit, setOrbit] = useState(false),
    [view, setView] = useState("perspective");
  const [partId, setPartId] = useState(""),
    [parts, setParts] = useState<Part[]>([]),
    [group, setGroup] = useState("Engines");
  latest.current = { components, selected, onSelect, part: partId };
  const controller = useRef<{
    view: (v: string) => void;
    cutaway: (v: boolean) => void;
    orbit: (v: boolean) => void;
  } | null>(null);
  const profile = useMemo(() => profileFor(aircraftType), [aircraftType]);
  const chosen = parts.find(
    (p) =>
      p.id === partId && (!p.component || selected.endsWith("/" + p.component)),
  );
  function selectPart(p: Part) {
    setPartId(p.id);
    setGroup(p.group);
    if (p.component) {
      const c = components.find((c) =>
        c.component_id.endsWith("/" + p.component),
      );
      if (c) onSelect(c.component_id);
    }
    if (p.internal) {
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
    setOrbit(false);
    setView("perspective");
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
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.15;
    element.replaceChildren(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 100);
    camera.position.set(14, 8, -18);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.enablePan = false;
    controls.minDistance = 9;
    controls.maxDistance = 34;
    controls.maxPolarAngle = Math.PI * 0.52;
    controls.target.set(0, 0.3, 0);
    controls.autoRotateSpeed = 0.55;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8c8980, 2.1));
    const key = new THREE.DirectionalLight(0xfff6e9, 3.2);
    key.position.set(-6, 12, -8);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    Object.assign(key.shadow.camera, {
      left: -11,
      right: 11,
      top: 11,
      bottom: -11,
    });
    key.shadow.bias = -0.0005;
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xdbe5ec, 1.5);
    fill.position.set(8, 4, 6);
    scene.add(fill);
    const model = buildAircraft(aircraftType);
    scene.add(model.root);
    setParts(model.parts);
    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(100, 100),
      new THREE.ShadowMaterial({ opacity: 0.18 }),
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -1.32;
    ground.receiveShadow = true;
    scene.add(ground);
    function cut(enabled: boolean) {
      for (const o of model.shells) {
        const m = o.material as THREE.MeshStandardMaterial;
        m.transparent = enabled;
        m.opacity = enabled ? 0.13 : 1;
        m.depthWrite = !enabled;
      }
      for (const o of model.internals) o.visible = enabled;
    }
    controller.current = {
      view(mode) {
        controls.target.set(0, 0.3, 0);
        camera.position.set(
          ...((mode === "top"
            ? [0, 23, 0.01]
            : mode === "side"
              ? [23, 3, 0]
              : [14, 8, -18]) as [number, number, number]),
        );
        controls.update();
      },
      cutaway: cut,
      orbit(enabled) {
        controls.autoRotate = enabled;
      },
    };
    const ray = new THREE.Raycaster();
    let down = [0, 0];
    const pointerDown = (e: PointerEvent) => {
      down = [e.clientX, e.clientY];
    };
    const pointerUp = (e: PointerEvent) => {
      if (Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5) return;
      const r = renderer.domElement.getBoundingClientRect();
      ray.setFromCamera(
        new THREE.Vector2(
          ((e.clientX - r.left) / r.width) * 2 - 1,
          (-(e.clientY - r.top) / r.height) * 2 + 1,
        ),
        camera,
      );
      // Intersect the visible skin too, so a click cannot select hidden equipment through it.
      const targets = model.meshes.filter(
        (o) =>
          o.visible &&
          !(
            model.shells.includes(o) &&
            (o.material as THREE.MeshStandardMaterial).opacity < 0.5
          ),
      );
      const hit = ray.intersectObjects(targets, false)[0];
      const part = model.parts.find((p) => p.id === hit?.object.userData.part);
      if (part) selectRef.current(part);
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
    const reduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    let frame = 0,
      last = 0;
    function animate(t: number) {
      frame = requestAnimationFrame(animate);
      if (document.hidden || t - last < 32) return;
      last = t;
      for (const fan of model.fans) if (!reduced) fan.rotation.z = t * 0.0003;
      const active = model.parts.find((p) => p.id === latest.current.part);
      const exact =
        active &&
        (!active.component ||
          latest.current.selected.endsWith("/" + active.component));
      for (const o of model.meshes) {
        const part = model.parts.find((p) => p.id === o.userData.part);
        const selectedPart = exact
          ? part?.id === active.id
          : !!part?.component &&
            latest.current.selected.endsWith("/" + part.component);
        const m = o.material as THREE.MeshStandardMaterial;
        m.emissive.set(selectedPart ? 0x746344 : 0x000000);
        m.emissiveIntensity = selectedPart ? 0.16 : 0;
      }
      controls.update();
      renderer.render(scene, camera);
    }
    frame = requestAnimationFrame(animate);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      controls.dispose();
      renderer.dispose();
      renderer.domElement.removeEventListener("pointerdown", pointerDown);
      renderer.domElement.removeEventListener("pointerup", pointerUp);
      const geometries = new Set<THREE.BufferGeometry>(),
        materials = new Set<THREE.Material>();
      scene.traverse((o) => {
        if (o instanceof THREE.Mesh) {
          geometries.add(o.geometry);
          (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) =>
            materials.add(m),
          );
        }
      });
      geometries.forEach((g) => g.dispose());
      materials.forEach((m) => m.dispose());
      element.replaceChildren();
      controller.current = null;
    };
  }, [aircraftType]);
  return (
    <div className="aircraft-study">
      {failed ? (
        <div className="scene-fallback">
          {fallback}
          <p>
            3D unavailable on this device. System selection remains available
            below.
          </p>
        </div>
      ) : (
        <div className="aircraft-scene aircraft-studio">
          <div ref={host} className="scene-canvas" />
          <div className="study-caption">
            <span className="study-kicker">AIRCRAFT REFERENCE</span>
            <strong>{profile?.label ?? "Generic narrow-body"}</strong>
            <span>
              {profile
                ? `${profile.length} m length · ${profile.span.toFixed(2)} m span`
                : "Unrecognized aircraft type"}
            </span>
          </div>
          <div className="study-variant">
            {profile ? "Type-based geometry" : "Generic reference geometry"}
            <br />
            Illustrative configuration
          </div>
          <div className="scene-tools">
            <button
              aria-pressed={cutaway}
              onClick={() => {
                setCutaway(!cutaway);
                controller.current?.cutaway(!cutaway);
              }}
            >
              Cutaway
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
                {mode === "perspective" ? "3/4 view" : mode}
              </button>
            ))}
            <button
              aria-pressed={orbit}
              onClick={() => {
                setOrbit(!orbit);
                controller.current?.orbit(!orbit);
              }}
            >
              Rotate
            </button>
          </div>
          <div className="scene-hint">
            Drag to orbit · Scroll to zoom · Select a part
          </div>
        </div>
      )}
      <div className="anatomy-header">
        <strong>Explore the assembly</strong>
        <span>{parts.length} reference parts · 6 monitored systems</span>
      </div>
      <div className="anatomy-tabs" aria-label="Assembly groups">
        {[
          "Engines",
          "Airframe",
          "Flight controls",
          "Landing gear",
          "Avionics",
        ].map((g) => (
          <button
            key={g}
            aria-pressed={group === g}
            onClick={() => setGroup(g)}
          >
            {g}
          </button>
        ))}
      </div>
      <div className="anatomy-parts">
        {parts
          .filter((p) => p.group === group)
          .map((p) => (
            <button
              key={p.id}
              aria-pressed={chosen?.id === p.id}
              onClick={() => selectPart(p)}
            >
              <span>{p.label}</span>
              <small>
                {p.internal ? "Cutaway · " : ""}
                {p.component ? "System-linked" : "Geometry only"}
              </small>
            </button>
          ))}
      </div>
      <div className="anatomy-note">
        {chosen ? (
          <>
            <strong>{chosen.label}</strong>
            <p>{chosen.description}</p>
            <span>
              {chosen.component
                ? `Telemetry source: ${chosen.component.replaceAll("_", " ")} · subpart health is not measured separately.`
                : "No health telemetry is assigned to this structural reference."}
            </span>
          </>
        ) : (
          <p>
            Select a part to inspect its construction and associated system.
            Installed variants and internal layouts are illustrative.
          </p>
        )}
      </div>
    </div>
  );
}
