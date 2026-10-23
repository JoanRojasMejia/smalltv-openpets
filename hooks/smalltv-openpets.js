// OpenCode plugin: record each session's state for the SmallTV bridge.
// Installed by copying into ~/.config/opencode/plugins/. Writes the same per-session
// JSON files as hooks/claude_hook.py (see session_store.py) and never throws.

import { mkdir, rename, rm, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

const STATE_DIR =
  process.env.SMALLTV_STATE_DIR ||
  join(process.env.XDG_STATE_HOME || join(homedir(), ".local", "state"), "smalltv-openpets", "sessions");

const TEST_COMMAND =
  /(^|[\s;&|(/])(pytest|py\.test|unittest|jest|vitest|mocha|rspec|phpunit|playwright\s+test|go\s+test|cargo\s+test|dotnet\s+test|mvn\s+test|gradlew?\s+test|(npm|pnpm|yarn|bun)\s+(run\s+)?test)\b/;

const EVENT_STATES = {
  "session.created": "idle",
  "permission.asked": "waiting",
  "permission.updated": "waiting",
  "question.asked": "waiting",
  "permission.replied": "working",
  "question.replied": "working",
  "question.rejected": "working",
  "session.idle": "done",
  "session.error": "error",
};

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
  if (type.startsWith("question")) return "has a question";
  const tool = props.permission ?? props.type ?? props.tool;
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

async function handleEvent(event) {
  const type = event?.type;
  const props = event?.properties ?? {};
  const sessionID = props.sessionID ?? props.info?.id;
  if (type === "session.deleted") return removeState(sessionID);
  const state = EVENT_STATES[type];
  if (state) return writeState(sessionID, state, process.cwd(), state === "waiting" ? waitingDetail(type, props) : "");
}

export const SmalltvOpenpetsPlugin = async ({ directory } = {}) => ({
  "chat.message": async (input) => writeState(input?.sessionID, "thinking", directory),
  "tool.execute.before": async (input, output) => {
    const state = classifyTool(input?.tool, output?.args);
    return writeState(input?.sessionID, state, directory, state === "waiting" ? "has a question" : "");
  },
  event: async ({ event }) => handleEvent(event),
});
