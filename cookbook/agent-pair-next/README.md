# Next agent pair

Open one fresh agent pair for the next-session prompt you explicitly select, leaving the source pair intact.

## What it does

Invoke `agterm-next` or explicitly ask “open this prompt in a new pair” to hand the last selected next-session prompt to a new left/right pair.
The new session sits immediately after the source in its window and workspace, with both panes in one confirmed directory.
Each side keeps its confirmed current engine, provider/profile and model, but starts a fresh conversation without source history.
The left is the only writer; the right waits as a read-only peer and responds through existing peer-chat.

BMAD's suggested next step is one use case, not an integration: this recipe changes neither BMAD nor peer-chat.
A recommendation to use a fresh context never launches anything automatically.
Permission to launch is not permission to implement: “launch the next brainstorming step” authorizes brainstorming, not project edits.
Normal task, tool, trust and authentication approvals still apply to both agents.

## Requirements

- agterm with **both unreleased local extensions** `session.new.split-command` and `session.type.expect-pane-id`, and a matching native `agtermctl` that accepts `session new --split-command` and guarded `session type --expect-pane-id`.
  There is no released minimum version for paired creation yet; **v0.28 alone does not establish support**.
  The serving app must advertise both exact markers in `result.app.capabilities` from `version` (also available in `result.tree.app.capabilities` from `tree`).
  Either missing marker means unsupported, regardless of the CLI binary's version.
- Python 3.10 or later, including the standard library; no Python packages are needed by the launcher.
- Existing executable `peer-chat.py`, its per-engine peer-chat skills, and existing `~/.config/agterm/peer-chat.json` with both profiles configured.
- Two runnable agents: OpenCode, Claude Code (including confirmed provider wrappers), or Codex CLI; same-engine pairs are supported.
  Their native initial-prompt and `--model` arguments must be supported.
- Exact full source-session and window UUIDs, an explicit absolute socket path, and a source with both foreground programs observable in a live split (a hidden split is allowed).
  Confirm current models and profiles with the user or authoritative runtime context. Foreground startup `--model` arguments are **not** evidence of the current model after an in-session switch.

## Setup

These are optional manual installation steps, not instructions to restart or install anything automatically.
From this recipe directory, choose a directory on your `PATH` as `BIN_DIR` and install the one shared launcher:

```sh
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
install -m 755 agterm-next.py "$BIN_DIR/agterm-next.py"
```

Ensure your agent's tool environment can resolve that directory on `PATH`.
For each engine you use, copy the corresponding skill; the installed filename must be exactly `SKILL.md`:

```sh
mkdir -p "$HOME/.config/opencode/skills/agterm-next" \
  "$HOME/.claude/skills/agterm-next" "$HOME/.codex/skills/agterm-next"
install -m 644 SKILL-opencode.md "$HOME/.config/opencode/skills/agterm-next/SKILL.md"
install -m 644 SKILL-claude.md "$HOME/.claude/skills/agterm-next/SKILL.md"
install -m 644 SKILL-codex.md "$HOME/.codex/skills/agterm-next/SKILL.md"
```

Do not add profile configuration or credentials for this recipe.
The existing peer-chat map supplies profile identities, `kind` and foreground process `command` basenames only; it does not select a launcher or a model.
Supply those explicitly in each payload.
A named Claude shell function can identify the source profile, but the launcher cannot execute or evaluate shell functions.
Use a user-confirmed trusted, non-daemonizing local executable wrapper that preserves that profile and forwards native arguments without resume or permission-bypass options.
Daemonizing or remote wrappers that break the original local agent's process ancestry are unsupported.
If its equivalent is unknown, ask; do not substitute plain `claude`, evaluate shell configuration, or copy credential environments.

## Usage

Tell your agent: “Use agterm-next for the last selected next-session prompt” or “Open this prompt in a new pair.”
If there are multiple candidates, no selected prompt, uncertain source IDs, different source directories, or an unknown current model/profile, it asks before creating anything.
Use the source pair's common directory; if the directories differ, confirm one existing absolute directory for both new panes.
Never resolve the source by GUI `active`, session name, UUID prefix, or cwd matching.

For a manual launch, set these variables to verified values:

| Variable | Value |
|---|---|
| `AGTERMCTL` | Matching native CLI executable, not the request relay |
| `SOURCE`, `WINDOW` | Exact full source-session and open-window UUIDs |
| `SOCKET` | Explicit absolute socket path for that app |
| `PEER_CHAT` | Installed executable peer-chat path |
| `PEER_CONFIG` | Existing `$HOME/.config/agterm/peer-chat.json` |
| `STATE_DIR` | Dedicated absolute request-state directory, owned by you, mode `700` |
| `REQUEST` | Fresh unique ID of 1–80 ASCII letters, digits, underscores or hyphens; never reuse |
| `PAYLOAD` | Exact absolute path of a private input JSON file you own, mode `600` |

Before launch, query the serving app with the native CLI and confirm both capability markers:

```sh
"$AGTERMCTL" version --socket "$SOCKET" --json
```

Write the payload using a JSON serializer, preserving the selected prompt's text and newlines exactly.
The following is a schema example, not a runnable profile selection: replace every uppercase placeholder with a confirmed value.
`kind` must be `opencode`, `claude` or `codex`; each side has exactly these four keys, with no extra flags.

```json
{
  "prompt": "EXACT SELECTED NEXT-SESSION PROMPT",
  "cwd": "/ABSOLUTE/CONFIRMED/DIRECTORY",
  "left": {"kind": "opencode", "profile": "CONFIRMED_LEFT_PROFILE", "model": "CONFIRMED_LEFT_MODEL", "executable": "CONFIRMED_LEFT_EXECUTABLE"},
  "right": {"kind": "claude", "profile": "CONFIRMED_RIGHT_PROFILE", "model": "CONFIRMED_RIGHT_MODEL", "executable": "CONFIRMED_RIGHT_EXECUTABLE"}
}
```

Create the private input file with mode `600` from the outset (for example, with an exclusive create under `umask 077`), not a public file made private later.
Keep the exact owned input path so you can remove it after `start` returns, on success or failure.
The launcher does not enforce the input file's mode or delete it for you.
After all launch inputs and the task's authorization scope are confirmed:

```sh
agterm-next.py start --payload "$PAYLOAD" --source "$SOURCE" \
  --window "$WINDOW" --socket "$SOCKET" --agtermctl "$AGTERMCTL" \
  --peer-chat "$PEER_CHAT" --peer-config "$PEER_CONFIG" \
  --state-dir "$STATE_DIR" --request "$REQUEST"
```

Remove only the exact owned `PAYLOAD` input file after this command returns, **not** `STATE_DIR/REQUEST`.
Report the returned session ID and per-side process observations; on failure, report the request, session ID (possibly `UNKNOWN`) and stage/side verbatim.
Do not retry under a new request ID after an ambiguous error, timeout or interruption: a pair may already exist.
Inspect that request manually before deciding what to do next; never silently create another pair or close the partial one.

## How it works

Preflight checks the executable paths, existing profile kinds, explicit source programs/window and both serving-app capabilities.
It does not infer or verify live model choices; confirmation happens before you construct the payload.
One `session new --command LEFT --split-command RIGHT --after SOURCE --no-select` creates the pair with explicit socket/window and common cwd.
After verifying structure and observing both processes, the launcher selects the new session and focuses left.
It never types a launch line into a shell or composer.
The pane launcher starts through `/usr/bin/env` so a login-style `argv[0]` from the terminal does not break relocatable Python installations.
The new pair is independent after creation: closing or swapping the source does not invalidate it.
Its own original `paneID`/`splitPaneID` model UUIDs are pinned; swapping, promoting or replacing the new panes stops subsequent handoff/chat checks.

Each pane starts its agent through native CLI arguments: `--model` plus `--prompt` for OpenCode, or a positional bootstrap prompt for Claude/Codex.
No continue, resume, fork or safety-bypass flags are added.
The bootstrap tells the agent to run its generated `consume` command once and follow its output.
The launcher records the original exec process's PID and birth identity; `consume` and the relay require actual descendants of that still-live process.
Replaying restored bootstrap argv under a different process does not authorize claiming an unread packet.
The left's output ends with the unchanged original task after collaboration instructions; the right gets only instructions to acknowledge locally and wait.
The task body is not embedded in persisted terminal launch commands.

Agent harnesses can drop inherited sender variables.
The bootstrap therefore includes explicit new `AGTERM_*` identities, and `consume` supplies a peer-chat command with the complete environment and exact new session/window/peer pane.
For **every** peer message, use that generated command, including its private `AGTERMCTL` relay and `AGTERM_NEXT_*` values; the relay pins the actual control calls to the correct socket.
Each relayed type call also asserts the peer's original token with `--pane left|right --expect-pane-id UUID`, so even same-engine pairs cannot silently target a swapped or replacement pane through that call.
The app checks the positional slot and realized surface token atomically with input delivery; it refuses a mismatch rather than redirecting input to the token's new slot.
Guarded typing neither selects, realizes nor polls a surface, even with `--select`.
Do not replace it with a bare peer-chat invocation, cwd discovery, or raw `agtermctl session type`.
This explicit command may require normal tool approval even where ordinary peer-chat prefixes were previously approved.

Request directories are mode `700`; their files are mode `600`.
Consumption claims each packet before output, deletes its task file, and retains launch/consumption tombstones and metadata.
A small detached child watchdog watches its parent, the original agent; when that agent exits, it invalidates pending task packets and exits itself.
Caught startup or ambiguous creation failures also erase pending task packets, retaining non-sensitive metadata, tombstones and diagnostics rather than task bodies.
Retained state records the at-most-once handoff claim and supports the relay while the request remains active.
Do not blindly clean request state while either pane is alive, and never remove tombstones to reuse an ID.
If consumption fails or reports already consumed, stop rather than reconstructing or repeating the task.

## Limits

- The launcher creates a session and eventually changes selection; it never closes the source or a partially started pair, kills processes, restarts the app, or changes restore preferences.
  Normal terminal process-exit behavior still applies.
- The right's read-only role is an instruction, **not a sandbox**. “Only writer” does not grant left permission to edit when the selected task is only discussion or planning.
- Startup observation defaults to 120 inspections spaced 0.25 seconds apart (roughly 30 seconds of polling, **plus control-command time**), not a hard 30-second deadline.
  `--inspections` accepts 1–240; `--timeout` defaults to 5 seconds per control call and accepts values greater than 0 up to 30.
  Observing foreground processes does not prove composer readiness, bootstrap consumption, peer-chat delivery or task completion.
- Existing trust, authentication and permission prompts can block startup. The user answers them; never send approval keys or add bypass flags.
  If the new pair's pane roles change, stop; the relay deliberately rejects stale identities.
- An at-most-once handoff claim is not exactly-once task execution: a process can fail after claiming but before output reaches the agent.
  Treat an already consumed task as potentially delivered; do not repeat it after a failure.
  Packet erasure does not erase agent transcripts or the caller-owned input file. Abrupt failures outside the caught failure/watchdog paths still require inspection, not a blanket cleanup or retry.
- The launcher has 59 fake-process regression tests in `test_agterm_next.py`.
  An isolated native-app smoke also verified both handoffs, guarded-input swap rejection and an OpenCode/Claude Code peer-chat round trip, with manual permission approval.
  Other provider wrappers, Codex's actual sandbox and installed-skill discovery still need their own interactive checks.
