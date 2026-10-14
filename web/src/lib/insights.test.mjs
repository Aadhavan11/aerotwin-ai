// Run when Node and the existing web dev dependencies are available:
// node --test web/src/lib/insights.test.mjs
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { mock } from "node:test";
import ts from "typescript";

// Compile only the pure helper module in memory; no generated files are written.
const source = await readFile(new URL("./insights.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const { weibullCurve, delaysByTail } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

test("Weibull CDF has 60 finite, rounded points and the expected endpoints", () => {
  const curve = weibullCurve(100, 2, 30);
  assert.ok(curve);
  assert.equal(curve.extent, 200);
  const points = [...curve.path.matchAll(/([ML]) (\d+\.\d{2}) (\d+\.\d{2})/g)];
  assert.equal(points.length, 60);
  assert.equal(curve.path, points.map((point) => point[0]).join(" "));
  assert.equal(points.at(0)?.[0], "M 30.00 110.00");
  const last = points.at(-1);
  assert.ok(last);
  assert.equal(last[1], "L");
  assert.equal(last[2], "230.00");
  assert.equal(last[3], (110 - (1 - Math.exp(-4)) * 95).toFixed(2));
  let previousY = 110;
  for (const point of points) {
    const y = Number(point[3]);
    assert.ok(y >= 15 && y <= previousY);
    previousY = y;
  }
});

test("Weibull extent includes b10 when it exceeds twice eta", () => {
  assert.equal(weibullCurve(100, 1, 500)?.extent, 500);
});

test("invalid Weibull parameters and overflowing extent skip the chart", () => {
  for (const invalid of [0, -1, NaN, Infinity, -Infinity]) {
    assert.equal(weibullCurve(invalid, 2, 30), null);
    assert.equal(weibullCurve(100, invalid, 30), null);
  }
  assert.equal(weibullCurve(100, 2, NaN), null);
  assert.equal(weibullCurve(100, 2, Infinity), null);
  assert.equal(weibullCurve(Number.MAX_VALUE, 2, 30), null);
});

test("delay badges handle absent data and include only positive finite totals", () => {
  assert.deepEqual([...delaysByTail()], []);
  assert.deepEqual([...delaysByTail([])], []);
  const rows = [
    { tail: "SIM-017", legs_affected: 2, total_delay_min: 45 },
    { tail: "SIM-018", legs_affected: 0, total_delay_min: 0 },
    { tail: "SIM-019", legs_affected: 1, total_delay_min: -5 },
    { tail: "SIM-020", legs_affected: 1, total_delay_min: NaN },
    { tail: "SIM-021", legs_affected: 1, total_delay_min: Infinity },
  ];
  assert.deepEqual([...delaysByTail(rows)], [["SIM-017", 45]]);
});

test("weather API helpers use JSON POST, DELETE and handle empty 204 responses and failures", async () => {
  // Isolate the request helpers from React and Vite's build-time environment.
  const clientSource = (await readFile(new URL("../api/client.ts", import.meta.url), "utf8"))
    .replace(/^import .* from "react";\n/m, "")
    .replace("import.meta.env.VITE_API", "undefined");
  const compiled = ts.transpileModule(clientSource, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
  const client = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
  const calls = [];
  let response = new Response(null, { status: 204 });
  let networkFailure = false;
  const fetchMock = mock.method(globalThis, "fetch", async (path, init) => {
    calls.push({ path, init });
    if (networkFailure) throw new Error("offline");
    return response;
  });
  try {
    const result = await client.del("/insights/weather/storm-1");
    assert.equal(result.data, null);
    assert.deepEqual(calls.at(-1), { path: "/api/insights/weather/storm-1", init: { method: "DELETE" } });
    const body = { station: "DFW", start: "2026-10-13T12:00:00.000Z", duration_h: 1, severity: 2 };
    response = new Response("{}", { status: 200 });
    await client.post("/insights/weather", body);
    assert.deepEqual(calls.at(-1), {
      path: "/api/insights/weather",
      init: { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    });
    for (const status of [404, 500]) {
      response = new Response("unavailable", { status });
      await assert.rejects(client.get("/insights/disruptions"), (error) => error instanceof client.ApiError && error.status === status);
    }
    networkFailure = true;
    await assert.rejects(client.get("/insights/disruptions"), /offline/);
  } finally {
    fetchMock.mock.restore();
  }
});
