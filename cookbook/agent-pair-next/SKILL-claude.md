---
name: agterm-next
description: Use when the user explicitly asks to open this prompt in a new pair or invokes agterm-next.
---

# agterm-next for Claude Code

Launch only on an explicit request such as “open this prompt in a new pair” or `agterm-next`, never from a fresh-context recommendation.
BMAD is a possible source of the prompt, not something to modify.
Use the shared executable `agterm-next.py` on PATH; do not implement another launcher.

1. Resolve the last selected next-session prompt from the conversation; preserve it exactly, including newlines.
   If missing or ambiguous, ask. Separate launch authorization from task authorization:
   launching brainstorming does not authorize edits or implementation.
2. Require the source's exact full session and window UUIDs and an explicit absolute socket path.
   Use confirmed originating runtime identities or ask; never infer from `active`, names, prefixes or cwd.
   Confirm a common existing absolute source directory; if the panes have different directories, ask which to use for both.
3. Confirm each side's current engine, provider/profile, model and trusted executable with the user or authoritative runtime context.
   Source startup `--model` argv is not live model evidence. Never silently use defaults or copy captured launch/restore argv.
   A named Claude function needs a confirmed non-daemonizing local executable wrapper that forwards native arguments; never eval a function or guess its equivalent.
   Daemonizing or remote wrappers that break local agent ancestry are unsupported.
4. Require existing peer-chat executable/skills and `~/.config/agterm/peer-chat.json`.
   Its `agents[profile].kind` and `.command` identify engine and foreground basename, not launch executable or model.
   Do not add config or credentials. Require a matching native CLI and preflight
   `"$AGTERMCTL" version --socket "$SOCKET" --json`; `result.app.capabilities` must contain
   both `session.new.split-command` and `session.type.expect-pane-id`. These are unreleased local extensions, not guaranteed by v0.28.
   Stop if any prerequisite is unknown or absent; never install, launch or restart agterm automatically.
5. Create a private mode-600 owned JSON input using a serializer, with exactly `prompt`, `cwd`, `left`, `right`.
   Each side has exactly `kind` (`opencode`, `claude` or `codex`), `profile`, `model`, `executable`.
   Both profiles/models/executables must be explicit, even for same-engine pairs.
   Do not include credentials, extra flags, resume/continue/fork options or safety bypasses.
   Use a dedicated absolute mode-700 state directory and a unique request ID matching `[A-Za-z0-9_-]{1,80}`.
6. Set the variables below to those confirmed values (`PAYLOAD` is the exact owned input path;
   `AGTERMCTL` is the native CLI, not a relay), then run once:

   ```sh
   agterm-next.py start --payload "$PAYLOAD" --source "$SOURCE" \
     --window "$WINDOW" --socket "$SOCKET" --agtermctl "$AGTERMCTL" \
     --peer-chat "$PEER_CHAT" --peer-config "$PEER_CONFIG" \
     --state-dir "$STATE_DIR" --request "$REQUEST"
   ```

7. After `start` returns, remove only the exact owned input `PAYLOAD`, on success or failure.
   Keep non-sensitive request-state metadata, diagnostics and tombstones for the handoff claim and relay.
   Caught startup/ambiguous failures erase pending task packets, not this retained state.
   Report returned IDs/process observations or the exact failing request/session/side (including `UNKNOWN`).
   On ambiguous failure, timeout or interruption, stop for manual inspection; never retry with a new ID or close the partial pair.
   The default 120 inspections at 0.25-second intervals plus control-call time establish process observation, not agent readiness.

## New-pair contract

Both agents start fresh through native CLI bootstrap arguments, never typed launch text.
Run the generated `consume` command once and follow its output: left receives the unchanged original task after role instructions;
right acknowledges locally and waits for left, without independently starting the task.
Left is the only writer within the user's authorized task; right is read-only by instruction, not by sandbox.
Do not auto-answer trust/authentication/permission prompts or bypass normal approvals.
`consume` and the relay require actual descendants of the original live exec process, checked by PID and birth identity.
Restored argv under another process does not authorize an unread packet. A detached child watchdog invalidates pending packets and exits when the original agent dies.
This is an at-most-once handoff claim, not exactly-once task execution.
If `consume` fails or is already consumed, treat the task as potentially delivered; stop without reconstructing or repeating it.

A harness may drop sender environment variables. For every peer-chat send, use the exact command from `consume`,
including explicit new `AGTERM_*` values, `AGTERMCTL` private relay, `AGTERM_NEXT_*`, config and peer addresses.
Pipe only the message into its stdin; preserve this environment even if ordinary peer-chat instructions prefer bare commands.
Normal approval may be needed. Never fall back to cwd/active discovery or raw `agtermctl session type`.
The relay pins each type call to the peer's original `paneID`/`splitPaneID` token and explicit positional pane.
Guarded typing requires explicit left/right plus a UUID (not scratch). The app atomically refuses swapped/replaced slots or surfaces instead of redirecting input;
it never selects, realizes or polls a pane, even with `--select`.
Closing/swapping the source after creation does not affect the independent new pair.
If the NEW panes swap, are promoted or replaced, stop. Do not clean request state while the pair is alive or reuse request IDs.
