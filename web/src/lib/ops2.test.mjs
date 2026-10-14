// Compile in memory with the existing TypeScript dependency, like operations.test.mjs.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { mock } from "node:test";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const dataUrl = (source) => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
async function compile(path, imports = {}) {
  const source = (await readFile(new URL(path, import.meta.url), "utf8"))
    .replaceAll("import.meta.env.VITE_API", "undefined")
    .replaceAll("import.meta.env.BASE_URL", '"/"');
  const { outputText } = ts.transpileModule(source, {
    fileName: path,
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
  });
  return dataUrl(outputText.replace(/from "([^"]+)"/g, (_, specifier) =>
    `from ${JSON.stringify(imports[specifier] ?? import.meta.resolve(specifier))}`));
}
const ops = await import(await compile("./ops2.ts"));
const clientUrl = await compile("../api/client.ts");
const llp = await import(await compile("../api/llp.ts", { "./client": clientUrl }));
const mel = await import(await compile("../api/mel.ts", { "./client": clientUrl }));
const risk = await import(await compile("../api/risk.ts", { "./client": clientUrl }));

test("histogram scales counts, clamps negative bars and rejects malformed data", () => {
  const plot = ops.histogramPlot({ bin_edges: [0, 10, 20], counts: [2, 4] }, 200, 100);
  assert.equal(plot.max, 4);
  assert.deepEqual(plot.ticks, [0, 2, 4]);
  assert.deepEqual(plot.bars, [
    { x: 0, y: 50, width: 90, height: 50, label: "0–10" },
    { x: 100, y: 0, width: 90, height: 100, label: "10–20" },
  ]);
  for (const hist of [null, {}, { bin_edges: null, counts: null }, { bin_edges: [], counts: [] },
    { bin_edges: [0, 1, 2], counts: [1] }, { bin_edges: [0, Infinity], counts: [1] },
    { bin_edges: [0, NaN], counts: [1] }, { bin_edges: [2, 1], counts: [1] },
    { bin_edges: [1, 1], counts: [1] }, { bin_edges: [0, 1], counts: [null] },
    { bin_edges: [0, 1], counts: [Infinity] }]) {
    assert.equal(ops.histogramPlot(hist, 200, 100), null);
  }
  const empty = ops.histogramPlot({ bin_edges: [0, 1, 2], counts: [-2, 0] }, 200, 100);
  assert.equal(empty.max, 0);
  assert.ok(empty.bars.every((bar) => bar.height === 0 && bar.y === 100));
  assert.equal(ops.histogramPlot({ bin_edges: [0, 1], counts: [1] }, -1, 100), null);
});

test("pipeline uses one scale, preserves month order, and plots monotone cumulative paths", () => {
  const pipeline = Array.from({ length: 12 }, (_, index) => ({
    month: index + 1, expected: 1, cumulative_expected: index + 1, cumulative_p50_count: (index + 1) * 2,
  }));
  const plot = ops.pipelinePlot(pipeline, 600, 240);
  assert.equal(plot.max, 24);
  assert.deepEqual(plot.labels, Array.from({ length: 12 }, (_, index) => index + 1));
  assert.ok(plot.bars.every((bar) => bar.height === 10));
  assert.ok(plot.linePath.startsWith("M 25 230 L 75 220"));
  assert.ok(plot.dashedPath.endsWith("L 575 0"));
  for (const path of [plot.linePath, plot.dashedPath]) {
    const points = [...path.matchAll(/[ML] ([\d.]+) ([\d.]+)/g)].map((match) => [Number(match[1]), Number(match[2])]);
    assert.equal(points.length, 12);
    points.slice(1).forEach(([x, y], index) => { assert.ok(x > points[index][0]); assert.ok(y <= points[index][1]); });
  }
  const missing = ops.pipelinePlot([null, { month: 3, expected: NaN, cumulative_expected: null }, { month: 1, expected: 5, cumulative_expected: 7, cumulative_p50_count: Infinity }], 200, 100);
  assert.deepEqual(missing.labels, [3, 1]);
  assert.equal(missing.max, 7);
  assert.equal(missing.linePath, "M 150 0");
  assert.equal(missing.dashedPath, "");
  assert.equal(missing.bars[0].height, 0);
  for (const input of [null, undefined, []]) assert.deepEqual(ops.pipelinePlot(input, 200, 100), { bars: [], labels: [], linePath: "", dashedPath: "", max: 1 });
});

test("expiry countdown uses the supplied simulation clock and exact expiry boundary", () => {
  const now = "2026-01-01T00:00:00Z";
  for (const [expires, label] of [["2026-01-03T04:00:00Z", "2d 4h"], ["2026-01-01T05:12:00Z", "5h 12m"], ["2026-01-01T00:38:00Z", "38m"]]) {
    assert.deepEqual(ops.expiryCountdown(expires, now), { expired: false, label });
  }
  for (const expires of [now, "2025-12-31T23:59:59Z"]) assert.deepEqual(ops.expiryCountdown(expires, now), { expired: true, label: "expired" });
  for (const [expires, simNow] of [[null, now], [now, null], [undefined, now], ["invalid", now], [now, "invalid"]]) {
    assert.deepEqual(ops.expiryCountdown(expires, simNow), { expired: false, label: "—" });
  }
});

test("API details prefer detail, support error envelopes and role denials, and bound raw text", () => {
  assert.equal(ops.apiDetail(new Error('422 /ops/mel: {"detail":"Positive integer required","error":"invalid"}')), "Positive integer required");
  assert.equal(ops.apiDetail(new Error('404 /ops/montecarlo: {"error":"not_found"}')), "not_found");
  for (const code of [401, 403]) assert.equal(ops.apiDetail(new Error(`${code} /ops/mel: denied`)), "Not available for your role.");
  for (const value of [null, undefined, "offline", {}, new Error("offline")]) assert.equal(ops.apiDetail(value), "Not available. Please try again.");
  const raw = "x".repeat(250);
  assert.equal(ops.apiDetail(new Error(`500 /ops/mel: ${raw}`)), raw.slice(0, 200));
  assert.equal(ops.apiDetail(new Error('422 /ops/mel: {"detail":[')), '{"detail":[');
});

test("operations APIs encode filters and IDs and send exact mutation bodies", async () => {
  const calls = [];
  const payload = { simulated: true };
  const fetchMock = mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(payload));
  });
  const path = () => calls.at(-1).url;
  const body = () => JSON.parse(calls.at(-1).init.body);
  try {
    assert.deepEqual(await llp.getLlp(), payload); assert.equal(path(), "/api/ops/llp");
    await llp.getLlp("SIM /1"); assert.equal(path(), "/api/ops/llp?tail=SIM%20%2F1");
    await llp.getLlpTail("SIM /1"); assert.equal(path(), "/api/ops/llp/SIM%20%2F1");
    await llp.getShopVisits(); assert.equal(path(), "/api/ops/shop-visits");
    await llp.getShopVisits("SIM/1"); assert.equal(path(), "/api/ops/shop-visits?tail=SIM%2F1");
    await mel.getMel(); assert.equal(path(), "/api/ops/mel");
    await mel.getMel({ tail: "SIM/1", status: "open" }); assert.equal(path(), "/api/ops/mel?tail=SIM%2F1&status=open");
    await mel.getMel({ tail: "SIM/1" }); assert.equal(path(), "/api/ops/mel?tail=SIM%2F1");
    await mel.getMel({ status: "expired" }); assert.equal(path(), "/api/ops/mel?status=expired");
    await mel.getMelSummary(); assert.equal(path(), "/api/ops/mel/summary");
    await mel.closeMel("MEL/1"); assert.equal(path(), "/api/ops/mel/MEL%2F1/close"); assert.deepEqual(body(), {});
    await mel.extendMel("MEL/1", "Awaiting parts"); assert.equal(path(), "/api/ops/mel/MEL%2F1/extend"); assert.deepEqual(body(), { justification: "Awaiting parts" });
    const create = { tail: "SIM-001", component_kind: "avionics", fault_mode: "fault", reason: "Display fault", limit_days: 2 };
    await mel.createMel(create); assert.equal(path(), "/api/ops/mel"); assert.deepEqual(body(), create);
    await risk.runMonteCarlo({ n: 500, seed: 0 }); assert.equal(path(), "/api/ops/montecarlo"); assert.deepEqual(body(), { n: 500, seed: 0 });
    await risk.runMonteCarlo({ plan_id: "P-3", n: 100, seed: 1 }); assert.deepEqual(body(), { plan_id: "P-3", n: 100, seed: 1 });
    assert.ok(calls.filter((call) => call.init).every((call) => call.init.method === "POST" && call.init.headers["Content-Type"] === "application/json"));
  } finally { fetchMock.mock.restore(); }
});

// Render real page markup with settled request fixtures; no browser or extra packages needed.
const pageHarnessUrl = dataUrl(`
  let role = "ops", states = [], health = {};
  export function configure(nextRole, nextStates, nextHealth = {}) { role = nextRole; states = [...nextStates]; health = nextHealth; }
  export function useRole() { return { role: { id: role } }; }
  export function useAvailable() { return { loading: false, ...states.shift() }; }
  export function usePoll() { return health; }
`);
const pageHarness = await import(pageHarnessUrl);
const pageImports = {
  "../lib/useAvailable": pageHarnessUrl, "../lib/role": pageHarnessUrl, "../api/client": pageHarnessUrl,
  "../lib/operations": await compile("./operations.ts"), "../lib/ops2": await compile("./ops2.ts"),
  "../components/ui": await compile("../components/ui.tsx", { "../lib/format": await compile("./format.ts") }),
  "../api/llp": await compile("../api/llp.ts", { "./client": clientUrl }),
  "../api/mel": await compile("../api/mel.ts", { "./client": clientUrl }),
  "../api/risk": await compile("../api/risk.ts", { "./client": clientUrl }),
};
const Llp = (await import(await compile("../pages/Llp.tsx", pageImports))).default;
const Mel = (await import(await compile("../pages/Mel.tsx", pageImports))).default;
const Risk = (await import(await compile("../pages/Risk.tsx", pageImports))).default;

test("pages render missing and null response fields without crashing", () => {
  pageHarness.configure("ops", [{ data: { summary: null, parts: [null, {}] } }, {}, {}]);
  assert.match(renderToStaticMarkup(createElement(Llp)), /Life-limited parts/);
  pageHarness.configure("ops", [{ data: { payload: { items: [null, {}] } } }, { data: { by_category: null, aircraft_with_open_restrictions: null } }]);
  assert.match(renderToStaticMarkup(createElement(Mel)), /MEL register/);
  pageHarness.configure("ops", [{ data: { result: { cost_usd: null, histogram: null, risk_ranking: [null, {}], comparison: { optimised: null, baseline: null } } } }]);
  const riskMarkup = renderToStaticMarkup(createElement(Risk));
  assert.match(riskMarkup, /No valid histogram available/);
  assert.match(riskMarkup, /Optimised and baseline comparison/);
  pageHarness.configure("ops", [{ data: { result: { comparison: null } } }]);
  assert.doesNotMatch(renderToStaticMarkup(createElement(Risk)), /Optimised and baseline comparison/);
});

test("MEL and risk forms respect every role; MEL keeps full restrictions and simulation countdown", () => {
  for (const role of ["ops", "maintenance", "admin", "dispatch", "pilot"]) {
    const editable = ["ops", "maintenance", "admin"].includes(role);
    pageHarness.configure(role, [
      { data: { payload: { items: [{ id: "MEL-0001", category: "B", status: "open", extensions_used: 0, expires_at: "2026-01-03T04:00:00Z", restrictions: ["Full restriction: max FL 310 and manual checks each transit"] }] } } },
      { data: { by_category: {}, aircraft_with_open_restrictions: ["SIM-002", "SIM-001"] } },
    ], { data: { sim_now: "2026-01-01T00:00:00Z" } });
    const melMarkup = renderToStaticMarkup(createElement(Mel));
    assert.match(melMarkup, /Full restriction: max FL 310 and manual checks each transit/);
    assert.match(melMarkup, /2d 4h/);
    assert.match(melMarkup, /SIM-001, SIM-002/);
    assert.equal(melMarkup.includes('type="submit"'), editable);
    assert.equal(melMarkup.includes("Extension justification for MEL-0001"), editable);
    pageHarness.configure(role, [{}]);
    const riskMarkup = renderToStaticMarkup(createElement(Risk));
    assert.equal(riskMarkup.includes('type="submit"'), editable);
    if (!editable) assert.match(riskMarkup, /Not available for your role/);
  }
});
