// OpenCode plugin: record each session's state for the SmallTV bridge.
// Installed by copying into ~/.config/opencode/plugins/. Writes the same per-session
// JSON files as hooks/claude_hook.py (see session_store.py) and never throws.
//
// One dependency-free default export serves both OpenCode lines: V1 (1.18.29+) calls
// server() and uses the returned hooks; V2 calls setup(ctx). Both discover this file
// in ~/.config/opencode/plugins/.

import { mkdir, rename, rm, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

const STATE_DIR =
  process.env.SMALLTV_STATE_DIR ||
  join(process.env.XDG_STATE_HOME || join(homedir(), ".local", "state"), "smalltv-openpets", "sessions");

const TEST_COMMAND =
  /(^|[\s;&|(/])(pytest|py\.test|unittest|jest|vitest|mocha|rspec|phpunit|playwright\s+test|go\s+test|cargo\s+test|dotnet\s+test|mvn\s+test|gradlew?\s+test|(npm|pnpm|yarn|bun)\s+(run\s+)?test)\b/;

// V1 bus events carry `properties`; V2 events carry `data`. V2 questions are forms.
const EVENT_STATES = {
  "session.created": "idle",
  "permission.asked": "waiting",
  "permission.updated": "waiting",
  "question.asked": "waiting",
  "form.created": "waiting",
  "permission.replied": "working",
  "question.replied": "working",
  "question.rejected": "working",
  "form.replied": "working",
  "form.cancelled": "working",
  "session.idle": "done",
  "session.error": "error",
  "session.execution.failed": "error",
};

// V2 rejects unknown names in the subscription filter, so list only V2 event types.
const V2_EVENT_TYPES = [
  "session.created",
  "session.deleted",
  "permission.asked",
  "permission.replied",
  "form.created",
  "form.replied",
  "form.cancelled",
  "session.idle",
  "session.execution.failed",
];

function classifyTool(tool, args) {
  const name = String(tool || "").toLowerCase();
  if (name === "question") return "waiting";
  if (/edit|write|patch/.test(name)) return "working";
  if (/^(read|grep|glob|list|webfetch|websearch|codesearch)$/.test(name)) return "thinking";
  if (/bash|shell|terminal/.test(name)) {
    return TEST_COMMAND.test(String(args?.command ?? "")) ? "testing" : "working";
  }
  return "working";
}

function waitingDetail(type, props) {
  if (type.startsWith("question") || type.startsWith("form")) return "has a question";
  const tool = props.permission ?? props.action ?? props.type ?? props.tool;
  return typeof tool === "string" && tool ? `allow ${tool}?` : "needs permission";
}

function fileFor(sessionID) {
  return join(STATE_DIR, `opencode-${String(sessionID).replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 110)}.json`);
}

async function writeState(sessionID, state, cwd = process.cwd(), detail = "") {
  if (!sessionID) return;
  try {
    await mkdir(STATE_DIR, { recursive: true });
    const target = fileFor(sessionID);
    const tmp = `${target}.${process.pid}.tmp`;
    const payload = { agent: "opencode", session: sessionID, state, ts: Date.now() / 1000, cwd, detail };
    await writeFile(tmp, JSON.stringify(payload));
    await rename(tmp, target);
  } catch {}
}

async function removeState(sessionID) {
  if (!sessionID) return;
  try {
    await rm(fileFor(sessionID), { force: true });
  } catch {}
}

async function handleEvent(event, cwd = process.cwd()) {
  const type = event?.type;
  const props = event?.properties ?? event?.data ?? {};
  const sessionID = props.sessionID ?? props.info?.id;
  if (type === "session.deleted") return removeState(sessionID);
  const state = EVENT_STATES[type];
  if (state) return writeState(sessionID, state, cwd, state === "waiting" ? waitingDetail(type, props) : "");
}

function recordTool(sessionID, tool, args, directory) {
  const state = classifyTool(tool, args);
  return writeState(sessionID, state, directory, state === "waiting" ? "has a question" : "");
}

// V1 entry: returns the hooks object.
async function server({ directory } = {}) {
  return {
    "chat.message": async (input) => writeState(input?.sessionID, "thinking", directory),
    "tool.execute.before": async (input, output) => recordTool(input?.sessionID, input?.tool, output?.args, directory),
    event: async ({ event }) => handleEvent(event),
  };
}

// V2 entry: subscribes to the event stream and registers hooks; returns the cleanup.
// Hook handlers don't await the write, so they never delay the prompt or the tool.
async function setup(ctx) {
  const directory = ctx?.location?.directory ?? process.cwd();
  const controller = new AbortController();
  const registrations = [];
  let consumer = Promise.resolve();
  try {
    const events = ctx.event.subscribe({ types: V2_EVENT_TYPES, signal: controller.signal });
    consumer = (async () => {
      for await (const event of events) {
        if (controller.signal.aborted) break;
        await handleEvent(event, directory);
      }
    })().catch(() => {});
    registrations.push(await ctx.session.hook("prompt", (event) => void writeState(event?.sessionID, "thinking", directory)));
    registrations.push(
      await ctx.tool.hook("execute.before", (event) => void recordTool(event?.sessionID, event?.tool, event?.input, directory)),
    );
  } catch {}
  return async () => {
    controller.abort();
    await Promise.allSettled([consumer, ...registrations.map((registration) => registration?.dispose?.())]);
  };
}

export default { id: "smalltv-openpets", server, setup };
