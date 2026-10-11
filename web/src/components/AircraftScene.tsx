import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import type { TwinComponent } from "../api/types";
import { profileFor } from "../lib/aircraft";

/**
 * The 3D viewer. The only aircraft geometry is the third-party model in public/models/b737-800 (CC BY 4.0, see docs/ASSETS.md):
 * "B737-800 Model" by hikami3150. Every tail is drawn with it; an A320-class tail says so on screen.
 */
const MODEL_URL = `${import.meta.env.BASE_URL}models/b737-800/scene.gltf`;
const CREDIT_URL = "https://sketchfab.com/3d-models/b737-800-model-6cbe380405794ea69d6b0a3d144dd1af";

/** Close-up of one system (an engine): the camera goes to it, its skin turns see-through and a marker sits at `fraction` (0 front, 1 back) along its axis. */
export type Focus = { component: string; fraction: number; title: string; note: string; marker: boolean };

type Props = {
  aircraftType: string;
  components: TwinComponent[];
  selected: string;
  onSelect: (id: string) => void;
  fallback: ReactNode;
  focus?: Focus | null;
};

export default function AircraftScene({ aircraftType, components, selected, onSelect, fallback, focus }: Props) {
  const focusRef = useRef(focus);
  focusRef.current = focus;
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef({ components, selected, onSelect });
  latest.current = { components, selected, onSelect };
  const [failed, setFailed] = useState(false),
    [loaded, setLoaded] = useState(false),
    [orbit, setOrbit] = useState(false),
    [view, setView] = useState("perspective");
  const controller = useRef<{ view: (v: string) => void; orbit: (v: boolean) => void } | null>(null);
  const profile = useMemo(() => profileFor(aircraftType), [aircraftType]);
  const drawnAs737 = !!profile && !/B738|737/i.test(aircraftType);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    setFailed(false);
    setLoaded(false);
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
    renderer.shadowMap.type = THREE.PCFShadowMap;
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
    controls.maxDistance = 38;
    controls.maxPolarAngle = Math.PI * 0.52;
    controls.target.set(0, 0.3, 0);
    controls.autoRotateSpeed = 0.55;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8c8980, 2.1));
    const key = new THREE.DirectionalLight(0xfff6e9, 3.2);
    key.position.set(-6, 12, -8);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    Object.assign(key.shadow.camera, { left: -11, right: 11, top: 11, bottom: -11 });
    key.shadow.bias = -0.0005;
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xdbe5ec, 1.5);
    fill.position.set(8, 4, 6);
    scene.add(fill);
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(100, 100), new THREE.ShadowMaterial({ opacity: 0.18 }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -1.32;
    ground.receiveShadow = true;
    scene.add(ground);

    // Pieces we can tell apart by where they sit: the engines (left and right) and the landing gear. They glow by system status
    // and select their system when clicked. Everything else is plain airframe.
    const groups: Record<string, THREE.Mesh[]> = { engine_1: [], engine_2: [], landing_gear: [] };
    const meshes: THREE.Mesh[] = [];
    let disposed = false;
    let pulse: THREE.Mesh | null = null;
    new GLTFLoader().load(
      MODEL_URL,
      (gltf) => {
        if (disposed) return;
        const root = gltf.scene;
        root.updateMatrixWorld(true);
        const box = new THREE.Box3().setFromObject(root);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        const bounds = new THREE.Box3();
        const c = new THREE.Vector3();
        // Classified in the coordinates as authored: x across, y up, z along the fuselage with the tail at -z. Authored x > 0 ends up at
        // world x < 0 after the half turn below, which is engine_1.
        root.traverse((o) => {
          const mesh = o as THREE.Mesh;
          if (!mesh.isMesh) return;
          mesh.castShadow = true;
          mesh.receiveShadow = true;
          bounds.setFromObject(mesh).getCenter(c);
          const engine = Math.abs(c.x) > 3.5 && Math.abs(c.x) < 4.7 && c.y > 0.7 && c.y < 2.5 && c.z > -5.6 && c.z < 0.3;
          const group = engine ? (c.x > 0 ? "engine_1" : "engine_2") : c.y < 1.35 ? "landing_gear" : "";
          if (group) {
            mesh.material = (mesh.material as THREE.Material).clone();
            mesh.userData.component = group;
            groups[group].push(mesh);
          }
          meshes.push(mesh);
        });
        const length = (profile?.length ?? 39.5) / 2.7;
        const holder = new THREE.Group();
        root.position.set(-center.x, -box.min.y, -center.z);
        holder.add(root);
        holder.scale.setScalar(length / size.z);
        holder.rotation.y = Math.PI; // the model's nose is +z, the scene's nose is -z
        holder.position.y = -1.24; // tyre bottoms
        scene.add(holder);
        const f = focusRef.current;
        if (f) {
          holder.updateMatrixWorld(true);
          const group = groups[f.component] ?? [];
          if (group.length) {
            const box = new THREE.Box3();
            for (const m of group) box.expandByObject(m);
            const center = box.getCenter(new THREE.Vector3());
            const extent = box.getSize(new THREE.Vector3());
            const size3 = (m: THREE.Mesh) => new THREE.Box3().setFromObject(m).getSize(new THREE.Vector3());
            const nacelle = [...group].sort((a, b) => size3(b).x * size3(b).y * size3(b).z - size3(a).x * size3(a).y * size3(a).z)[0];
            const ns = size3(nacelle);
            const mat = nacelle.material as THREE.MeshStandardMaterial;
            mat.transparent = true; // see into the engine so the marker shows
            mat.opacity = 0.32;
            mat.depthWrite = false;
            // fade the rest of the aircraft so the engine stands alone
            const inGroup = new Set<THREE.Object3D>(group);
            for (const m of meshes) {
              if (inGroup.has(m)) continue;
              const other = m.material as THREE.MeshStandardMaterial;
              other.transparent = true;
              other.opacity = 0.08;
              other.depthWrite = false;
            }
            const at = new THREE.Vector3(center.x, center.y, box.min.z + f.fraction * extent.z);
            if (f.marker) {
              const red = (opacity: number) => new THREE.MeshBasicMaterial({ color: 0xff3b3b, transparent: true, opacity, depthTest: false });
              const dot = new THREE.Mesh(new THREE.SphereGeometry(0.075, 24, 16), red(0.95));
              dot.position.copy(at);
              dot.renderOrder = 10;
              const ring = new THREE.Mesh(new THREE.TorusGeometry((Math.max(ns.x, ns.y) / 2) * 1.02, 0.012, 12, 72), red(0.85));
              ring.position.copy(at);
              ring.renderOrder = 9;
              scene.add(dot, ring);
              pulse = dot;
            }
            const side = Math.sign(center.x) || 1;
            camera.position.set(center.x + side * 3.0, center.y + 1.1, center.z - 3.0);
            controls.target.copy(center);
            controls.minDistance = 1.2;
            controls.maxDistance = 9;
            controls.update();
          }
        }
        setLoaded(true);
      },
      undefined,
      () => {
        if (!disposed) setFailed(true);
      },
    );

    controller.current = {
      view(mode) {
        controls.target.set(0, 0.3, 0);
        camera.position.set(...((mode === "top" ? [0, 32, 0.01] : mode === "side" ? [23, 3, 0] : [14, 8, -18]) as [number, number, number]));
        controls.update();
      },
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
      ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, (-(e.clientY - r.top) / r.height) * 2 + 1), camera);
      const comp = ray.intersectObjects(meshes, false)[0]?.object.userData.component as string | undefined;
      const hit = comp ? latest.current.components.find((x) => x.component_id.endsWith("/" + comp)) : undefined;
      if (hit) latest.current.onSelect(hit.component_id);
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
    let frame = 0,
      last = 0;
    const tintOf = new THREE.Color();
    function animate(t: number) {
      frame = requestAnimationFrame(animate);
      if (document.hidden || t - last < 32) return;
      last = t;
      // Glow the engines and gear by status; the selected system is brighter.
      for (const [comp, group] of Object.entries(groups)) {
        const state = latest.current.components.find((x) => x.component_id.endsWith("/" + comp));
        const picked = latest.current.selected.endsWith("/" + comp);
        let color = 0x000000,
          level = 0;
        if (state?.status === "alert") [color, level] = [0xff4d4d, picked ? 0.8 : 0.5];
        else if (state?.status === "watch") [color, level] = [0xffb020, picked ? 0.6 : 0.3];
        else if (picked) [color, level] = [0x4c8dff, 0.5];
        const tint = state?.status === "alert" ? 0xff8a8a : state?.status === "watch" ? 0xffd27a : 0xffffff;
        for (const m of group) {
          const mat = m.material as THREE.MeshStandardMaterial;
          mat.emissive.set(color);
          mat.emissiveIntensity = level;
          // a white nacelle only turns pink under a red glow, so tint the paint too
          mat.color.set(m.userData.base ?? (m.userData.base = mat.color.getHex()));
          if (tint !== 0xffffff) mat.color.lerp(tintOf.set(tint), 0.6);
        }
      }
      if (pulse) pulse.scale.setScalar(1 + 0.3 * Math.sin(t * 0.005));
      controls.update();
      renderer.render(scene, camera);
    }
    frame = requestAnimationFrame(animate);
    return () => {
      disposed = true;
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
          (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => materials.add(m));
        }
      });
      geometries.forEach((g) => g.dispose());
      materials.forEach((m) => m.dispose());
      element.replaceChildren();
      controller.current = null;
    };
  }, [aircraftType, profile, focus?.component, focus?.fraction, focus?.marker]);

  return (
    <div className="aircraft-study">
      {failed ? (
        <div className="scene-fallback">
          {fallback}
          <p>The 3D model could not be loaded on this device. The system diagram is shown instead.</p>
        </div>
      ) : (
        <div className={`aircraft-scene aircraft-studio ${focus ? "focus" : ""}`}>
          <div ref={host} className="scene-canvas" />
          {!loaded && <div className="scene-loading-overlay">Loading the 3D model…</div>}
          <div className="study-caption">
            <span className="study-kicker">{focus ? "Close-up" : "Aircraft"}</span>
            <strong>{focus ? focus.title : (profile?.label ?? "Narrow-body aircraft")}</strong>
            <span>{focus ? focus.note : profile ? `${profile.length} m length · ${profile.span.toFixed(2)} m span` : "Type not recognized"}</span>
          </div>
          <div className="study-variant">
            {drawnAs737 ? "Drawn with a Boeing 737-800 model" : "Third-party 3D model"}
            <br />
            Illustrative configuration
          </div>
          <a className="scene-credit" href={CREDIT_URL} target="_blank" rel="noreferrer">
            3D model “B737-800 Model” by hikami3150 · CC BY 4.0
          </a>
          <div className="scene-tools">
            {(focus ? [] : ["perspective", "top", "side"]).map((mode) => (
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
          <div className="scene-hint">Drag to orbit · Scroll to zoom · Click an engine or the landing gear</div>
        </div>
      )}
    </div>
  );
}
