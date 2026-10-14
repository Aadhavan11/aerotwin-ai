// Like insights.test.mjs, compile the TypeScript modules in memory using existing dependencies.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { mock } from "node:test";
import ts from "typescript";

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

const clientUrl = await compile("../api/client.ts");
const client = await import(clientUrl);
const reliability = await import(await compile("../api/reliability.ts", { "./client": clientUrl }));
const runs = await import(await compile("../api/runs.ts", { "./client": clientUrl }));
const operations = await import(await compile("./operations.ts"));
const hooks = await import(await compile("./useAvailable.ts", { "../api/client": clientUrl }));

test("undefined metrics stay undefined, alert severity ordering and overall weighted NFF match the API", () => {
  for (const value of [null, undefined, NaN, Infinity]) assert.equal(operations.metric(value), "—");
  assert.equal(operations.metric(0), "0");
  assert.equal(operations.worstAlert([]), null);
  const severity = ["normal", "insufficient_data", "warning", "alert"];
  for (let i = 0; i < severity.length; i++) {
    assert.equal(operations.worstAlert(severity.slice(0, i + 1).reverse().map((status) => ({ status }))), severity[i]);
  }
  assert.equal(operations.overallNff([
    { unscheduled_removals: 2, nff_removals: 1 },
    { unscheduled_removals: 8, nff_removals: 1 },
  ]), 0.2);
  assert.equal(operations.overallNff([]), null);
  assert.equal(operations.overallNff([{ unscheduled_removals: 0, nff_removals: 0 }]), null);
  assert.equal(operations.overallNff([{ unscheduled_removals: null, nff_removals: 1 }]), null);
  assert.equal(operations.overallNff([{ unscheduled_removals: 2, nff_removals: null }]), null);
});

test("Pareto preserves response order and uses independent count and cumulative scales", () => {
  const plot = operations.paretoPlot([
    { ata_chapter: "32", unscheduled_removals: 6, cumulative_share: 0.6 },
    { ata_chapter: "24", unscheduled_removals: 4, cumulative_share: 1 },
  ]);
  assert.equal(plot.max, 6);
  assert.deepEqual(plot.bars.map((bar) => bar.height), [180, 120]);
  assert.equal(plot.line, "M 180 102 L 440 30");
  assert.deepEqual(operations.paretoPlot([]), { max: 1, bars: [], line: "" });
  const missing = operations.paretoPlot([
    { unscheduled_removals: 0, cumulative_share: 0 },
    { unscheduled_removals: null, cumulative_share: null },
    { unscheduled_removals: NaN, cumulative_share: Infinity },
  ]);
  assert.deepEqual(missing.bars.map((bar) => bar.height), [0, 0, 0]);
  assert.ok(!/NaN|Infinity/.test(missing.line));
});

test("run validation, progress and resume eligibility follow the runner contract", () => {
  for (const status of ["running", "queued"]) assert.equal(operations.runActive({ status }), true);
  for (const status of ["done", "failed", "interrupted"]) assert.equal(operations.runActive({ status }), false);
  for (const status of ["failed", "interrupted"]) assert.equal(operations.runResumable({ status }), true);
  for (const status of ["done", "running", "queued"]) assert.equal(operations.runResumable({ status }), false);
  assert.equal(operations.runProgress({ cycles_done: 2, total_cycles: 8 }), 0.25);
  assert.equal(operations.runProgress({ cycles_done: 0, total_cycles: 0 }), null);
  assert.equal(operations.runProgress({ cycles_done: NaN, total_cycles: 8 }), null);
  assert.equal(operations.runProgress({ cycles_done: 10, total_cycles: 8 }), 1);
  assert.equal(operations.validRunInput("1", "1234"), true);
  assert.equal(operations.validRunInput("20", "-1"), true);
  for (const years of ["", "0", "-1", "21", "Infinity"]) assert.equal(operations.validRunInput(years, "1234"), false);
  for (const seed of ["", "1.5", "NaN", "9007199254740992"]) assert.equal(operations.validRunInput("1", seed), false);
});

test("typed API helpers support both run list shapes and send the exact run requests", async () => {
  const calls = [];
  let body = [];
  const fetchMock = mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), { status: 200 });
  });
  try {
    const run = { id: "run/1", status: "queued" };
    for (const response of [[run], { runs: [run] }]) {
      body = response;
      assert.deepEqual(await runs.getRuns(), [run]);
    }
    for (const response of [null, {}, { runs: null }]) {
      body = response;
      assert.deepEqual(await runs.getRuns(), []);
    }
    body = run;
    assert.deepEqual(await runs.startRun(1.5, 1234), run);
    assert.deepEqual(calls.at(-1), { url: "/api/sim/runs", init: {
      method: "POST", headers: { "Content-Type": "application/json" }, body: '{"years":1.5,"seed":1234}',
    } });
    await runs.getRun(run.id);
    assert.equal(calls.at(-1).url, "/api/sim/runs/run%2F1");
    await runs.resumeRun(run.id);
    assert.equal(calls.at(-1).url, "/api/sim/runs/run%2F1/resume");
    assert.equal(calls.at(-1).init.method, "POST");
    assert.equal(calls.at(-1).init.body, "{}");
    await reliability.getReliabilityPack();
    assert.equal(calls.at(-1).url, "/api/insights/reliability/pack");
    await reliability.getAtaTable();
    assert.equal(calls.at(-1).url, "/api/insights/ata");
  } finally { fetchMock.mock.restore(); }
});

test("401/403/404/500 and offline requests give graceful messages, including export failures", async () => {
  const previousWindow = globalThis.window;
  const events = [];
  globalThis.window = { dispatchEvent: (event) => events.push(event.type) };
  let status = 401;
  const fetchMock = mock.method(globalThis, "fetch", async () => new Response("unavailable", { status }));
  try {
    for (const [code, message] of [[401, "Not available for your role."], [403, "Not available for your role."], [404, "Not available: not found."], [500, "Not available: server error."]]) {
      status = code;
      for (const action of [reliability.getReliabilityPack, reliability.getAtaTable, runs.getRuns, () => runs.getRun("1"), () => runs.startRun(1, 1234), () => runs.resumeRun("1"), () => runs.exportRun("1")]) {
        await assert.rejects(action(), (error) => hooks.unavailableMessage(error) === message);
      }
    }
    assert.equal(events.length, 7);
    assert.ok(events.every((event) => event === "aerotwin:auth-lost"));
    fetchMock.mock.mockImplementation(async () => { throw new Error("offline"); });
    await assert.rejects(runs.getRuns(), (error) => hooks.unavailableMessage(error) === "Not available. Please try again.");
  } finally {
    fetchMock.mock.restore();
    if (previousWindow === undefined) delete globalThis.window; else globalThis.window = previousWindow;
  }
});

test("export fetches a blob with cookie-client semantics and the server's download filename", async () => {
  let type = "application/vnd.apache.parquet";
  let disposition = 'attachment; filename="sim_run_1.parquet"';
  const fetchMock = mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(url, "/api/sim/runs/run%2F1/export");
    assert.equal(init, undefined);
    return new Response("telemetry", { headers: { "Content-Type": type, "Content-Disposition": disposition } });
  });
  try {
    const file = await runs.exportRun("run/1");
    assert.equal(file.filename, "sim_run_1.parquet");
    assert.equal(await file.blob.text(), "telemetry");
    type = "application/gzip";
    disposition = "";
    assert.equal((await runs.exportRun("run/1")).filename, "sim_run_run/1.csv.gz");
  } finally { fetchMock.mock.restore(); }
});

// A small hook harness lets Node exercise request lifetimes without a browser or new packages.
const reactHarnessUrl = dataUrl(`
  export const effects = [], states = [];
  export function useState(initial) { const index = states.push(initial) - 1; return [initial, next => { states[index] = next; }]; }
  export function useEffect(effect) { effects.push(effect); }
  export function reset() { effects.length = 0; states.length = 0; }
`);
const harness = await import(reactHarnessUrl);
const hook = await import(await compile("./useAvailable.ts", { react: reactHarnessUrl, "../api/client": clientUrl }));
const flush = () => new Promise(setImmediate);

test("active-run polling uses 2s intervals, skips overlapping requests and stops on completion or unmount", async () => {
  const intervals = new Map();
  let nextId = 0;
  const set = mock.method(globalThis, "setInterval", (callback, ms) => {
    assert.equal(ms, 2000);
    intervals.set(++nextId, callback);
    return nextId;
  });
  const clear = mock.method(globalThis, "clearInterval", (id) => intervals.delete(id));
  let resolve;
  let calls = 0;
  const load = () => { calls++; return new Promise((done) => { resolve = done; }); };
  let cleanup;
  try {
    harness.reset();
    hook.useAvailable(load, 0, operations.runActive);
    cleanup = harness.effects[0]();
    assert.equal(harness.states[0].loading, true);
    resolve({ status: "queued" });
    await flush();
    assert.equal(harness.states[0].data.status, "queued");
    assert.equal(intervals.size, 1);
    const tick = [...intervals.values()][0];
    tick(); tick();
    assert.equal(calls, 2);
    resolve({ status: "done" });
    await flush();
    assert.equal(intervals.size, 0);
    assert.equal(harness.states[0].data.status, "done");
    cleanup();
    harness.reset();
    hook.useAvailable(load, 0, operations.runActive);
    cleanup = harness.effects[0]();
    resolve({ status: "running" });
    await flush();
    [...intervals.values()][0]();
    cleanup();
    const last = harness.states[0];
    resolve({ status: "done" });
    await flush();
    assert.equal(intervals.size, 0);
    assert.equal(harness.states[0], last);
  } finally { cleanup?.(); set.mock.restore(); clear.mock.restore(); }
});

test("fetch failures end the loading state; a cancelled slow backfill never updates state", async () => {
  harness.reset();
  hook.useAvailable(async () => { throw new client.ApiError(500, "error"); });
  let cleanup = harness.effects[0]();
  await flush();
  assert.equal(harness.states[0].loading, false);
  assert.equal(harness.states[0].error, "Not available: server error.");
  cleanup();
  harness.reset();
  let resolve;
  hook.useAvailable(() => new Promise((done) => { resolve = done; }));
  cleanup = harness.effects[0]();
  cleanup();
  const last = harness.states[0];
  resolve({ simulated: true });
  await flush();
  assert.equal(harness.states[0], last);
});
