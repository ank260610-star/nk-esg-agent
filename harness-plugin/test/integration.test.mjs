import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { readFile } from "node:fs/promises";
import { AGENT_TOOL_WHITELIST, PARAMETERS } from "../dist/tools.js";
import { BusinessClient, hostKey } from "../dist/client.js";
import { apply } from "../dist/index.js";
import { parameterSchemaSpecToJsonSchema } from "@deepseek-ai/dsh-tools";

const success = {
  ok: true,
  data: {},
  error: null,
  task_id: "task_A",
  revision: 7,
};
const failure = (code, retryable = false) => ({
  ok: false,
  data: null,
  error: { code, message: "offline failure", retryable },
  task_id: "task_A",
  revision: 7,
});
async function serve(handler, run) {
  const server = http.createServer(handler);
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  try {
    await run(`http://127.0.0.1:${server.address().port}`);
  } finally {
    server.closeAllConnections();
    await new Promise((r) => server.close(r));
  }
}
function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

test("all ten tool inputs match canonical property and required sets without task override", async () => {
  const canonical = JSON.parse(
    await readFile(
      new URL("../../contracts/schema.json", import.meta.url),
      "utf8",
    ),
  ).$defs;
  assert.equal(AGENT_TOOL_WHITELIST.length, 10);
  for (const tool of AGENT_TOOL_WHITELIST) {
    const projection = parameterSchemaSpecToJsonSchema(PARAMETERS[tool]);
    const expected = canonical[`${tool}_input`];
    assert.deepEqual(
      Object.keys(projection.properties).sort(),
      Object.keys(expected.properties)
        .filter((k) => k !== "task_id")
        .sort(),
      tool,
    );
    assert.deepEqual(
      (projection.required || []).sort(),
      (expected.required || []).filter((k) => k !== "task_id").sort(),
      tool,
    );
  }
});

test("plugin registers ten real definitions and a monotonic guard", () => {
  const definitions = [],
    guards = [],
    events = [];
  apply({
    on: (name, listener) => events.push({ name, listener }),
    tools: {
      register: (x) => definitions.push(x),
      guard: (x) => guards.push(x),
    },
  });
  assert.deepEqual(
    definitions.map((t) => t.name),
    AGENT_TOOL_WHITELIST,
  );
  assert.equal(events[0].name, "agent/created");
  const restrictions = [];
  for (const session of ["new", "resume"])
    events[0].listener({
      agent: { ctx: { tools: { restrict: (r) => restrictions.push(r) } } },
    });
  assert.deepEqual(restrictions, [
    { allow: [...AGENT_TOOL_WHITELIST] },
    { allow: [...AGENT_TOOL_WHITELIST] },
  ]);
  for (const tool of [
    "bash",
    "pwsh",
    "write",
    "edit",
    "web_fetch",
    "web_search",
    "subagent",
    "run_code",
    "save_metric_candidates",
  ])
    assert.ok(guards[0]({ name: tool }));
  for (const name of AGENT_TOOL_WHITELIST)
    assert.equal(guards[0]({ name }), undefined);
});

test("write retries transient response using exactly one host UUID", async () => {
  const keys = [],
    bodies = [];
  await serve(
    (req, res) => {
      let body = "";
      req.on("data", (b) => (body += b));
      req.on("end", () => {
        keys.push(req.headers["idempotency-key"]);
        bodies.push(body);
        send(
          res,
          keys.length === 1 ? 503 : 200,
          keys.length === 1 ? failure("MODEL_UNAVAILABLE", true) : success,
        );
      });
    },
    async (url) => {
      const c = new BusinessClient(url, "offline-token", "task_A", 1000, 1);
      assert.equal(
        (
          await c.call("extract_metrics", {
            expected_revision: 7,
            metric_ids: ["E01"],
            years: [2024],
          })
        ).ok,
        true,
      );
    },
  );
  assert.equal(keys.length, 2);
  assert.equal(keys[0], keys[1]);
  assert.equal(bodies[0], bodies[1]);
  assert.match(
    keys[0],
    /^[0-9a-f]{8}-[0-9a-f]{4}-[45][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  assert.equal(
    Object.hasOwn(JSON.parse(bodies[0]).arguments, "idempotency_key"),
    false,
  );
});

test("business conflict does not retry and transport failure stops after three attempts", async () => {
  let count = 0;
  await serve(
    (_req, res) => {
      count++;
      send(res, 409, failure("REVISION_CONFLICT"));
    },
    async (url) => {
      const c = new BusinessClient(url, "", "task_A", 1000, 1);
      const r = await c.call("parse_report", { expected_revision: 7 });
      assert.equal(r.error.code, "REVISION_CONFLICT");
    },
  );
  assert.equal(count, 1);
  count = 0;
  await serve(
    (req) => {
      count++;
      req.socket.destroy();
    },
    async (url) => {
      const r = await new BusinessClient(url, "", "task_A", 1000, 1).call(
        "parse_report",
        { expected_revision: 7 },
      );
      assert.equal(r.ok, false);
      assert.equal(r.error.code, "INTERNAL_ERROR");
    },
  );
  assert.equal(count, 3);
});

test("external cancellation has no retry and does not claim rollback", async () => {
  let count = 0;
  await serve(
    (_req, _res) => {
      count++;
    },
    async (url) => {
      const controller = new AbortController();
      const pending = new BusinessClient(url, "", "task_A", 1000, 1).call(
        "parse_report",
        { expected_revision: 7 },
        { signal: controller.signal },
      );
      setTimeout(() => controller.abort(), 25);
      const r = await pending;
      assert.equal(r.error.code, "CANCELLED");
      assert.match(r.error.message, /unknown/);
    },
  );
  assert.equal(count, 1);
});

test("forbidden fields, invalid envelopes and wrong task cannot become success", async () => {
  let count = 0;
  await serve(
    (_req, res) => {
      count++;
      send(res, 200, { ...success, task_id: "other" });
    },
    async (url) => {
      const c = new BusinessClient(url, "", "task_A");
      assert.equal(
        (await c.call("get_task_results", { path: "/sensitive" })).error.code,
        "INVALID_INPUT",
      );
      assert.equal(count, 0);
      assert.equal((await c.getTaskResults()).error.code, "INTERNAL_ERROR");
    },
  );
  await serve(
    (_req, res) => {
      res.writeHead(502);
      res.end("<secret>");
    },
    async (url) => {
      const r = await new BusinessClient(url).getTaskResults();
      assert.equal(r.ok, false);
      assert.equal(JSON.stringify(r).includes("<secret>"), false);
    },
  );
});

test("API destination must be a direct loopback origin and host keys survive client recreation", () => {
  for (const url of [
    "https://example.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000/path",
    "http://token@127.0.0.1:8000",
    "http://127.0.0.1:8000?query",
  ])
    assert.throws(() => new BusinessClient(url), /LOOPBACK/);
  assert.equal(
    hostKey("token", "t", "extract_metrics", "session/call"),
    hostKey("token", "t", "extract_metrics", "session/call"),
  );
  assert.notEqual(
    hostKey("token", "t", "extract_metrics", "session/call"),
    hostKey("token", "t", "extract_metrics", "session/other"),
  );
});

test("cancellation while reading response body stays CANCELLED and permission errors hide task", async () => {
  await serve(
    (_req, res) => {
      res.writeHead(200, { "content-type": "application/json" });
      res.write("{");
    },
    async (url) => {
      const controller = new AbortController();
      const pending = new BusinessClient(url, "", "task_A", 1000, 1).call(
        "parse_report",
        { expected_revision: 7 },
        { signal: controller.signal },
      );
      setTimeout(() => controller.abort(), 25);
      assert.equal((await pending).error.code, "CANCELLED");
      const denied = await new BusinessClient(url, "", "task_A").call(
        "bash",
        {},
      );
      assert.equal(denied.error.code, "FORBIDDEN");
      assert.equal(denied.task_id, null);
    },
  );
});
