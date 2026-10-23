# smalltv-openpets

Shows the live status of your coding agents (Claude Code and OpenCode) on a GeekMagic SmallTV-Ultra, with your [OpenPets](https://github.com/OpenPetsHQ/openpets) pet talking through a speech bubble.

```
Claude Code hook ─┐
                  ├─> ~/.local/state/smalltv-openpets/sessions/<agent>-<session>.json ─> bridge.py ─> GET /set?img=/image/op-<state>.gif
OpenCode plugin ──┘
```

Each agent session writes its own state file. The bridge shows the most urgent state across all live sessions, so one agent waiting for you is never hidden by another that is still working:

| Priority | State | Bubble | Claude Code | OpenCode |
| --- | --- | --- | --- | --- |
| 1 | waiting | I need you! (blinking) | PermissionRequest, AskUserQuestion, permission Notification | permission.asked/updated, question.asked, `question` tool |
| 2 | error | Oops, it failed | StopFailure | session.error |
| 3 | testing | Running tests... | Bash running a test command | bash running a test command |
| 4 | working | On it! | edits, other tools, PostToolUse | edit/write/patch, other tools, permission/question replied |
| 5 | thinking | Let me think... | UserPromptSubmit, Read/Grep/Glob/Web* | chat.message, read/grep/glob/list/web* |
| 6 | done | All done! | Stop | session.idle |
| 7 | idle | Zzz... | SessionStart, idle-prompt Notification (also clears a session interrupted with Esc) | session.created |

A session's state expires (and stops counting) after 1 min for done, 5 min for error, 10 min for thinking/working/testing, 15 min for idle and 2 h for waiting, so the pet goes back to sleeping shortly after the last agent finishes. Claude's SessionEnd and OpenCode's session.deleted remove the session file; files untouched for a day are pruned.

The original mode that tails the OpenPets debug log is still available with `--source log`.

## Configuration

Personal settings live outside the repository in `~/.config/smalltv-openpets/config.json` (template: [`config.example.json`](config.example.json)):

| Key | Meaning |
| --- | --- |
| `host` | The screen's IP address (overridden by `--host` or `SMALLTV_HOST`). |
| `calcifer_user`, `calcifer_channel` | Optional. Slack ids of a PR review bot and the channel where it answers (see *Review bot verdicts*). |
| `pipeline_repos`, `my_emails` | Optional. CodeCommit repos whose pipelines to watch, and the commit emails that mark a deploy as yours. |
| `aws_profile` | Optional. AWS CLI profile for the pipeline queries (default profile otherwise). |

Secrets never go in files: the Slack token and the optional iCal address live in the macOS Keychain.

### Slack messages

With a Slack user token (`xoxp-`, scopes `im:history`, `mpim:history`, `search:read`, `users:read`) stored in the Keychain, the bridge shows your new DMs and mentions: the pet waves under a bubble with the sender and the text for 20 s, then the agent state returns. Several messages in one poll show the newest as "Name +N".

```bash
security add-generic-password -U -s smalltv-openpets-slack -a "$USER" -w "$SLACK_MCP_XOXP_TOKEN"
python3 tools/slack_probe.py   # metadata-only check of the token and search queries
```

Slack has no push for plain user tokens, so `slack_source.py` searches `to:me` and `<@you>` every 30 s (messages can take up to ~1 min to show). Only messages newer than the first poll are shown, and seen ids persist in `~/.local/state/smalltv-openpets/slack.json`. The token and message text are never logged. Disable with `--no-slack`.

**Review bot verdicts ("Calcifer").** Some teams use an agentic PR reviewer bot that answers in a review channel, in threads whose parent post mentions the PR author, without mentioning the author itself. With `calcifer_user` and `calcifer_channel` set, the bridge also searches that bot's messages in that channel and keeps only replies whose thread parent mentions you (your PRs). They get a special card for 45 s: a pixel fire sprite (original art), the verdict parsed from the bot's wording — APROBADO (green), APROBADO CON SUGERENCIAS (yellow), RECHAZADO (red), REVISION HUMANA (orange) or COMENTARIO — and the "Motivo", while the pet jumps, fails or waits accordingly. The "review @bot @you" trigger posts are not shown as mentions. Adapt `VERDICTS` in `slack_source.py` to your bot's phrasing.

Privacy: the message GIF (`/image/op-message.gif`) stays on the screen's filesystem until the next message replaces it, and the screen serves files without authentication to anyone on your network.

### Who needs you

When the screen shows "I need you!", the bridge also renders a detailed card in the background (~6 s) listing each waiting session as `<project folder> - <reason>`: `allow Bash?` (permission, with the tool name), `has a question` or `needs permission`. The hooks store the reason in the session file's `detail` field.

### Meeting reminders

The bridge shows a blue card 5 minutes before each timed meeting ("IN 5 MIN" … "IN 1 MIN", then "STARTING NOW" for 90 s) with the time and title, refreshing the calendar every 10 min. All-day, cancelled and declined events are skipped. Disable with `--no-calendar`.

Source: the macOS Calendar app (your Google account added in System Settings → Internet Accounts), read through a small EventKit helper. Build it once; macOS asks for Calendar access the first time the bridge runs it (System Settings → Privacy & Security → Calendars):

```bash
tools/build_calendar_helper.sh
```

An optional `calendars.txt` (one calendar name per line) limits reminders to the listed calendars (the log shows which calendars upcoming meetings come from); `tools/bin/calendar-events --list-calendars` prints the names. Rebuilding the helper changes its signature, so macOS may ask for access again.

Fallback when EventKit is not an option: a Google Calendar secret iCal address in the Keychain (`security add-generic-password -U -s smalltv-openpets-calendar -a "$USER" -w`), read by `tools/calendar_events.py` through `uv` with `icalendar` + `recurring-ical-events`. Many Google Workspace domains disable secret addresses.

### Idle summary: story-point budget

When no agent is busy, the idle screen shows the Jira task you have in progress (`jira_status`, default "En Progreso") instead of "Zzz...": key, story points, a progress bar and how many hours are left (or over). One story point is 4 working hours; work time counts from when the task entered the in-progress category, only inside `work_schedule` (default Mon–Fri 09:00–18:00, lunch 13:00–14:00). The bar is green below 75 %, yellow up to 100 % and red over budget. Below it, rows from the other workers (`status_board.py`): your next meeting (weekday when it is not today), today's agents (`4 AGENTS 2 ASKS`: sessions active today and times one asked for you, from `agent_stats.py`, reset at midnight), your last finished deploy and the last Slack message, each with a coarse age (`10M`, `2H`, `3D`). The pet sits small in the bottom-right corner and switches to its worried animation when the task is over budget. The card refreshes every 5 min and is uploaded only when its text changes. Disable with `--no-summary`.

Setup: `jira_site` (and optionally `jira_points_field`, `jira_status`, `work_schedule`) in the config, plus an Atlassian API token (https://id.atlassian.com/manage-profile/security/api-tokens) in the Keychain, with your Atlassian email as the account:

```bash
security add-generic-password -U -s smalltv-openpets-jira -a "you@company.com" -w   # paste the token when prompted
```

### Stand-up digest

`daily_digest.py` gathers what you did on a workday — your commits in `work_repos` (local clones, all branches, matched by `my_emails`), Jira tickets whose status changed, your task in progress and blocked tickets (`jira_blocked_status`), your deploys, the agents' Engram memories (excluding `daily_exclude_projects`) and their daily notes — and asks Claude Haiku (headless `claude -p`, no tools, hooks disabled, no saved session) for three short lists: **AYER**, **HOY** and **BLOQUEOS**. Without the model it falls back to the raw facts. Digests are saved per day in `~/.local/state/smalltv-openpets/daily/`.

The bridge generates each workday's digest at 17:50 (or the next morning if the Mac was off) and shows the previous workday's digest as three full-screen pages: when the workday starts (09:00) and 10 and 3 minutes before any meeting whose title contains `daily_meeting_match` (default "daily"). Disable with `--no-daily`.

Agents can add notes for the digest:

```bash
python3 daily_digest.py note "ABC-41: form validation done"
python3 daily_digest.py generate --date 2026-10-22   # build and print a digest by hand
```

### Production deploys → Jira

When a production pipeline (name contains PROD, not TEST) deploys a commit with bracketed ticket keys (`[ABC-41] ...`), the tickets assigned to you that are not Done yet trigger a blue **IN PROD: MOVE TO DONE?** card, and stay as a summary row (`ABC-41 DONE?`) until Jira shows them done (rechecked every 30 min).

### Deploys (AWS CodePipeline) and AWS SSO

With `pipeline_repos` and `my_emails` in the config, the bridge watches the CodePipeline pipelines whose CodeCommit source is one of those repos (discovered once a day; scanning every pipeline takes a few minutes) and polls their latest executions every 2 min. An execution is yours when its source commit was authored by one of `my_emails` or one of them appears in the commit message (squash merges list every author). When one of yours starts, a cyan **DEPLOYING...** card shows for 20 s and polling speeds up to every 45 s; when it ends, a green **DEPLOYED** card (`ENV: TICKET subject`) shows for 30 s, or a red **DEPLOY FAILED** card for 45 s. Repointing a CI environment to another branch needs nothing: pipelines are matched by repository, and authors are checked per execution. The first poll is a baseline, so old deploys are never shown.

It uses your normal AWS CLI credentials through boto3 (`tools/aws_pipelines.py`, run with `uv`; optional `aws_profile` in the config). When the SSO session expires, the screen shows **AWS LOGIN NEEDED — run `aws sso login`** and repeats it every 30 min until the session works again. Disable with `--no-pipelines`.

### Pet sync

The bridge checks the OpenPets default pet (`preferences.defaultPetId` in `~/Library/Application Support/@open-pets/desktop/openpets-state.json`) and the outfit it wears every 5 s. Outfits (OpenPets wardrobes: alternative spritesheets such as `outfits/halloween.webp`) are resolved like OpenPets does: a manual choice in `petWardrobePreferences` wins, otherwise the first matching schedule rule (dates, weekdays, time ranges). When the pet or outfit differs from the screen's (`gifs/.pet`, stored as `pet:outfit`), it runs `pet_sync.py` in the background. The GIF of the state currently on screen is rendered, uploaded and shown first (the new pet appears within a few seconds); the other six follow (about 40 s), then the bridge reloads the current state. A state change during the sync can briefly show the previous pet. A failed sync is retried after 10 min. Pets need a raster spritesheet (`.webp`/`.png`) in the OpenPets pets folder; the built-in Hoodie Cat lives inside the app bundle and is not supported. Disable with `--no-pet-sync`.

## Files

- `hooks/claude_hook.py` — Claude Code hook (prints nothing, always exits 0, ~30 ms).
- `hooks/smalltv-openpets.js` — OpenCode plugin.
- `session_store.py` — session file format, priority and expiry rules.
- `bridge.py` — the long-running bridge (`--dry-run` prints requests instead of sending them).
- `install_hooks.py` — installs/uninstalls both hooks. Dry run unless `--apply`; backs up `~/.claude/settings.json` first.
- `make_gifs.py` — renders the state GIFs (240x240, pet + speech bubble) from an OpenPets spritesheet into `gifs/`. `--style neon` (default), `sticker`, `rpg` or `soft`.
- `pixel_art.py` — 5x7 pixel font, 7x7 state icons and stepped-corner boxes used by the pixel-art styles.
- `states.py` — states: spritesheet row, bubble text and accent color.
- `setup_device.py` — uploads the GIFs and pins the Photo Album theme. Dry run unless `--apply`; `--replace` re-uploads existing GIFs.
- `slack_source.py` / `slack_notifier.py` — Slack poller (Keychain token, DMs + mentions) and the worker that renders/uploads the message GIF.
- `pet_sync.py` — renders + uploads a pet's GIFs (`--pet <id>`, default: the OpenPets pet); used by the bridge.
- `pipeline_source.py`, `tools/aws_pipelines.py` — CodePipeline deploy notifications and AWS SSO expiry.
- `config.py` — loads `~/.config/smalltv-openpets/config.json`.
- `calendar_source.py`, `tools/CalendarEvents.swift` — meeting reminders and the EventKit helper.
- `launchd/` — LaunchAgent template and installer (runs the bridge at login, restarts it if it dies).
- `DEVICE_API.md` — the SmallTV-Ultra HTTP API as observed on firmware V9.0.54.

## Usage

```bash
python3 install_hooks.py --apply                     # Claude Code + OpenCode hooks
uv run --with pillow python make_gifs.py --pet miss-minute
python3 setup_device.py --host <screen-ip> --replace --apply
python3 bridge.py --dry-run                          # watch without touching the screen
python3 -m unittest test_bridge test_sessions test_pet_sync test_slack test_calendar test_pipelines test_jira test_status_board test_agent_stats test_release_followup test_daily
uv run --with icalendar --with recurring-ical-events python -m unittest tools/test_calendar_events.py
node --test hooks/smalltv-openpets.test.mjs
```

OpenCode loads the plugin when it starts; restart running OpenCode sessions after installing.

## Service

```bash
launchd/install.sh                                   # installs com.<you>.smalltv-openpets
tail -f ~/Library/Logs/smalltv-openpets.log
```

Restart after editing code: `launchctl kickstart -k gui/$(id -u)/com.$(id -un).smalltv-openpets`.

Uninstall everything:

```bash
python3 install_hooks.py --uninstall --apply
launchd/install.sh --uninstall
```

macOS must allow Local Network access for the bridge's Python (System Settings → Privacy & Security → Local Network); without it requests fail with `No route to host`.

## Caveats

- Reserve the screen's IP in your router's DHCP settings; the bridge only knows the one in your config.
- The screen only shows stored files, so the bubble texts are baked into the GIFs; edit `states.py`, re-run `make_gifs.py` and `setup_device.py --replace --apply` to change them.

## License

[MIT](LICENSE). Pet artwork belongs to its authors: generated GIFs are not part of this repository.
