import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

const dir = mkdtempSync(join(tmpdir(), "smalltv-oc-"));
process.env.SMALLTV_STATE_DIR = dir;
const mod = await import("./smalltv-openpets.js");
const plugin = mod.default;
const hooks = await plugin.server({ directory: "/work/app" });

const detailOf = (id) => JSON.parse(readFileSync(join(dir, `opencode-${id}.json`), "utf8")).detail;
const stateOf = (id) => JSON.parse(readFileSync(join(dir, `opencode-${id}.json`), "utf8")).state;
const tool = (sessionID, name, args = {}) => hooks["tool.execute.before"]({ tool: name, sessionID, callID: "c" }, { args });
const event = (type, properties) => hooks.event({ event: { type, properties } });

test("only the dual V1/V2 plugin object is exported (V1 calls every exported function)", () => {
  assert.deepEqual(Object.keys(mod), ["default"]);
  assert.equal(plugin.id, "smalltv-openpets");
  assert.equal(typeof plugin.server, "function");
  assert.equal(typeof plugin.setup, "function");
});

test("prompt is thinking and records the session file format", async () => {
  await hooks["chat.message"]({ sessionID: "a1" }, {});
  const data = JSON.parse(readFileSync(join(dir, "opencode-a1.json"), "utf8"));
  assert.equal(data.agent, "opencode");
  assert.equal(data.session, "a1");
  assert.equal(data.state, "thinking");
  assert.equal(data.cwd, "/work/app");
  assert.equal(typeof data.ts, "number");
});

test("tools map to states", async () => {
  const cases = [
    ["edit", {}, "working"],
    ["apply_patch", {}, "working"],
    ["read", {}, "thinking"],
    ["grep", {}, "thinking"],
    ["bash", { command: "npm test" }, "testing"],
    ["bash", { command: "go test ./..." }, "testing"],
    ["bash", { command: "ls latest" }, "working"],
    ["question", {}, "waiting"],
    ["some_mcp_tool", {}, "working"],
  ];
  for (const [name, args, expected] of cases) {
    await tool("t1", name, args);
    assert.equal(stateOf("t1"), expected, name);
  }
});

test("bus events map to states and deletion removes the session", async () => {
  await event("permission.asked", { sessionID: "e1" });
  assert.equal(stateOf("e1"), "waiting");
  assert.equal(detailOf("e1"), "needs permission");
  await event("permission.asked", { sessionID: "e1", permission: "bash" });
  assert.equal(detailOf("e1"), "allow bash?");
  await event("question.asked", { sessionID: "e1" });
  assert.equal(detailOf("e1"), "has a question");
  await event("permission.replied", { sessionID: "e1" });
  assert.equal(stateOf("e1"), "working");
  assert.equal(detailOf("e1"), "");
  await event("session.idle", { sessionID: "e1" });
  assert.equal(stateOf("e1"), "done");
  await event("session.error", { sessionID: "e1" });
  assert.equal(stateOf("e1"), "error");
  await event("session.deleted", { info: { id: "e1" } });
  assert.ok(!readdirSync(dir).includes("opencode-e1.json"));
});

test("unknown or malformed events never throw", async () => {
  await event("session.diff", { sessionID: "x" });
  await hooks.event({});
  await hooks.event({ event: null });
  await hooks["chat.message"]({});
  await hooks["tool.execute.before"](undefined, undefined);
  assert.ok(!readdirSync(dir).includes("opencode-x.json"));
});

// Fake V2 context: hook handlers are captured, events are pushed into the subscription.
function fakeV2Context(directory) {
  const handlers = {};
  const queue = [];
  let wake = () => {};
  let subscribeOptions;
  const disposed = [];
  const register = (domain) => async (name, handler) => {
    handlers[`${domain}.${name}`] = handler;
    return { dispose: async () => disposed.push(`${domain}.${name}`) };
  };
  const ctx = {
    location: { directory },
    event: {
      async *subscribe(options) {
        subscribeOptions = options;
        while (!options.signal.aborted) {
          if (queue.length) yield queue.shift();
          else await new Promise((resolve) => { wake = resolve; options.signal.addEventListener("abort", resolve, { once: true }); });
        }
      },
    },
    session: { hook: register("session") },
    tool: { hook: register("tool") },
  };
  const settle = () => new Promise((resolve) => setTimeout(resolve, 20));
  const emit = async (type, data) => { queue.push({ id: "ev", type, data }); wake(); await settle(); };
  return { ctx, handlers, emit, settle, disposed, options: () => subscribeOptions };
}

test("V2 setup maps the prompt and tool hooks to states", async () => {
  const v2 = fakeV2Context("/work/v2");
  const stop = await plugin.setup(v2.ctx);
  await v2.handlers["session.prompt"]({ sessionID: "v2a", prompt: {} });
  await v2.settle();
  const data = JSON.parse(readFileSync(join(dir, "opencode-v2a.json"), "utf8"));
  assert.equal(data.state, "thinking");
  assert.equal(data.cwd, "/work/v2");
  await v2.handlers["tool.execute.before"]({ tool: "bash", sessionID: "v2a", input: { command: "pnpm test" } });
  await v2.settle();
  assert.equal(stateOf("v2a"), "testing");
  await v2.handlers["tool.execute.before"]({ tool: "edit", sessionID: "v2a", input: {} });
  await v2.settle();
  assert.equal(stateOf("v2a"), "working");
  await stop();
});

test("V2 events map to states and deletion removes the session", async () => {
  const v2 = fakeV2Context("/work/v2");
  const stop = await plugin.setup(v2.ctx);
  assert.ok(v2.options().types.includes("session.idle"));
  await v2.emit("session.created", { sessionID: "v2e", title: "t" });
  assert.equal(stateOf("v2e"), "idle");
  await v2.emit("permission.asked", { id: "p", sessionID: "v2e", action: "bash", resources: [] });
  assert.equal(stateOf("v2e"), "waiting");
  assert.equal(detailOf("v2e"), "allow bash?");
  await v2.emit("permission.replied", { sessionID: "v2e" });
  assert.equal(stateOf("v2e"), "working");
  await v2.emit("form.created", { id: "f", sessionID: "v2e", title: "Pick one" });
  assert.equal(stateOf("v2e"), "waiting");
  assert.equal(detailOf("v2e"), "has a question");
  await v2.emit("form.replied", { sessionID: "v2e" });
  assert.equal(stateOf("v2e"), "working");
  await v2.emit("session.idle", { sessionID: "v2e" });
  assert.equal(stateOf("v2e"), "done");
  await v2.emit("session.execution.failed", { sessionID: "v2e", message: "boom" });
  assert.equal(stateOf("v2e"), "error");
  await v2.emit("session.deleted", { sessionID: "v2e" });
  assert.ok(!readdirSync(dir).includes("opencode-v2e.json"));
  await stop();
});

test("V2 cleanup stops the subscription and disposes every hook", async () => {
  const v2 = fakeV2Context("/work/v2");
  const stop = await plugin.setup(v2.ctx);
  await stop();
  assert.ok(v2.options().signal.aborted);
  assert.deepEqual(v2.disposed.sort(), ["session.prompt", "tool.execute.before"]);
  await v2.emit("session.idle", { sessionID: "v2z" });
  assert.ok(!readdirSync(dir).includes("opencode-v2z.json"));
});

test("V2 setup survives a context without hooks or events", async () => {
  const stop = await plugin.setup({});
  await stop();
});
