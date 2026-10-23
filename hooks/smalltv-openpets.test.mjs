import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

const dir = mkdtempSync(join(tmpdir(), "smalltv-oc-"));
process.env.SMALLTV_STATE_DIR = dir;
const mod = await import("./smalltv-openpets.js");
const hooks = await mod.SmalltvOpenpetsPlugin({ directory: "/work/app" });

const detailOf = (id) => JSON.parse(readFileSync(join(dir, `opencode-${id}.json`), "utf8")).detail;
const stateOf = (id) => JSON.parse(readFileSync(join(dir, `opencode-${id}.json`), "utf8")).state;
const tool = (sessionID, name, args = {}) => hooks["tool.execute.before"]({ tool: name, sessionID, callID: "c" }, { args });
const event = (type, properties) => hooks.event({ event: { type, properties } });

test("only the plugin is exported (OpenCode calls every exported function)", () => {
  assert.deepEqual(Object.keys(mod), ["SmalltvOpenpetsPlugin"]);
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
