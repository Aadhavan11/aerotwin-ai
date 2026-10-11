import fs from "node:fs";
import assert from "node:assert/strict";
import ts from "typescript";
import * as THREE from "three";
const source = fs.readFileSync(
  new URL("../src/components/aircraftModels.ts", import.meta.url),
  "utf8",
);
const code = ts
  .transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  })
  .outputText.replace('"three"', JSON.stringify(import.meta.resolve("three")));
const { buildAircraft, profileFor } = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
const allowed = new Set([
  "engine_1",
  "engine_2",
  "brakes",
  "landing_gear",
  "hydraulics",
  "avionics",
]);
for (const type of ["A320-class", "B738-class", "Unknown"]) {
  const model = buildAircraft(type);
  assert.equal(new Set(model.parts.map((p) => p.id)).size, model.parts.length);
  for (const p of model.parts)
    assert.ok(!p.component || allowed.has(p.component));
  assert.equal(
    model.parts.find((p) => p.id === "nose-gear").component,
    "landing_gear",
  );
  for (const mesh of model.meshes) {
    for (const key of ["position", "normal"]) {
      const values = mesh.geometry.getAttribute(key)?.array;
      assert.ok(values);
      for (const value of values) assert.ok(Number.isFinite(value));
    }
    assert.ok(model.parts.some((p) => p.id === mesh.userData.part));
  }
  const box = new THREE.Box3().setFromObject(model.root),
    size = box.getSize(new THREE.Vector3());
  if (model.profile) {
    assert.ok(Math.abs(size.z - model.profile.length / 2.7) < 0.01);
    assert.ok(Math.abs(size.x - model.profile.span / 2.7) < 0.01);
  }
  assert.ok(model.internals.length > 10);
  assert.ok(model.internals.every((m) => !m.visible));
  console.log(
    type,
    model.parts.length + " parts",
    model.meshes.length + " meshes",
    "geometry and telemetry links verified",
  );
}
assert.equal(profileFor("Unknown"), null);
assert.notEqual(
  profileFor("A320-class").length,
  profileFor("B738-class").length,
);
