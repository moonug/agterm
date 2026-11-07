# Configurable peer chat

Let any two configured coding agents hold a conversation with each other in one agterm split. A generalization of [two-agent-chat](../two-agent-chat): instead of two hardcoded profiles (Claude Code left, Codex right), agents come from a JSON config — `claude`, `claude-zai`, `claude-minimax`, `codex`, `opencode`, or anything you add — each with a composer kind that tells the transport how to read its UI.

## What it does

Two agents run side by side in a split, one per pane, and talk to each other. Each side sends messages through `peer-chat.py`, which verifies the target pane's composer before typing, sends the body as bounded observed chunks, and confirms submission. You watch both halves; your part is naming the peer ("work with codex") and stepping in when a decision is yours.

What this fork adds over the original recipe:

- **Config-driven agents.** `~/.config/agterm/peer-chat.json` maps a name to a composer kind (`claude`, `codex`, `opencode`) and the process name agterm should look for. Wrapper scripts and zsh functions that exec the same binary (claude-zai/claude-minimax all show `claude`) work out of the box.
- **Any pane layout.** The original pins Claude left and Codex right. Here the target pane is resolved live: the split pane whose foreground runs the peer's command; when both panes run the same command, the pane opposite to the sender (via `AGTERM_PANE`), with `--pane left|right` as an explicit override.
- **The peer is found by its screen.** When no pane answers to the configured command, the script reads the panes instead and takes the one that shows a composer of the configured kind — a Claude Code build behind a wrapper (`bun …/cli.js`), an alias or a patched build works with no configuration, and the sender label follows the kind the screen shows. A build that draws its own caret block (agterm then reports the caret at column 0) is read by the composer's content instead of by the caret, so a draft is still refused.
- **Deferred delivery.** `--defer` stores the message in a private SQLite queue (`~/.local/state/agterm/peer-chat/`) and a short-lived worker types it in when the pinned target pane is confirmably ready — including right after the user answers a permission dialog there. Records are pinned to the observed pane (window, session, pane side, pane-id token, socket fingerprint) and are cancelled instead of retargeted when that pane disappears; a submit that cannot be proven becomes `uncertain` and is never repeated. See `--queue-status`, `--queue-cancel`, `--ttl` (default 30 minutes) and `--delivery-id` for idempotent retries. Requires agterm with the `session.type.pane-id` capability (0.28.0 or later).
- **opencode support.** A composer parser for opencode's boxed TUI (light/heavy borders, splash and conversation states, status rows, cursor-glyph and cwd decorations). Both opencode layouts are read: v1, whose status row names the agent, the model and the effort and whose cwd sits flush right inside the box, and v2, whose status row names the agent and the model with its provider and whose cwd sits under the box. Typing into a redrawing opencode is paced in small slices because single large writes occasionally drop a character; verification catches any loss and refuses to submit.
- **Recovery.** When verification fails, cleanup restores the empty composer even if the typed text got corrupted (bounded blind backspacing, proven safe because the pre-write composer was confirmed empty), and the send retries — but only after cleanup is confirmed.
- **Short inline messages.** OpenCode v1 and v2 both refuse messages over 400 normalized characters, including the sender label, before typing or queueing. Long input can leave an unconfirmed partial draft even when its tail is visible. Put longer content in a readable file and send a short message with its path; the script does not create or split that content.

## Requirements

- agterm 0.24.0 or later (`surface cursor`); `--defer` additionally needs the `session.type.pane-id` capability (0.28.0+). The script refuses to type without the former and refuses to defer without the latter.
- Python 3.10+.
- At least two agents installed and runnable: Claude Code (or a flavour launched through a wrapper), Codex CLI, and/or opencode.
- `agtermctl` on your PATH.

## Setup

1. Copy `peer-chat.py` somewhere on your `PATH`, keeping the executable bit.
2. Create `~/.config/agterm/peer-chat.json` (see `peer-chat.json` here for a starting point):

   ```json
   {"agents": {
     "claude":         {"kind": "claude",   "command": "claude"},
     "claude-zai":     {"kind": "claude",   "command": "claude"},
     "claude-minimax": {"kind": "claude",   "command": "claude"},
     "codex":          {"kind": "codex",    "command": "codex"},
     "opencode":       {"kind": "opencode", "command": "opencode"}
   }}
   ```

   `kind` picks the composer parser; `command` is the name matched against agterm's view of the pane's foreground process. A wrapper or zsh function that ends up running `claude` is still matched by `command: "claude"` — the agent name is then mostly documentation. When no pane answers to that name, the script falls back to the screen and takes the pane that shows a composer of the configured kind, so a build behind a wrapper needs no entry of its own.
3. Install the skills: `SKILL-claude.md` → `~/.claude/skills/peer-chat/SKILL.md` (covers claude and every wrapper flavour), `SKILL-codex.md` → `~/.codex/skills/peer-chat/SKILL.md`, `SKILL-opencode.md` → `~/.config/opencode/skills/peer-chat/SKILL.md` (v1) and `~/.config/opencode2/skills/peer-chat/SKILL.md` (v2). Each loader requires the installed file to be named exactly `SKILL.md`.
4. For Codex, add the two `peer-chat.py` prefix rules from [two-agent-chat's setup](../two-agent-chat) to `~/.codex/rules/default.rules` (one per `--to <name>` you plan to use, plus `--prepare-message`), and start Codex with its pane id injected:

   ```sh
   codex -c "shell_environment_policy.set.AGTERM_SESSION_ID=\"$AGTERM_SESSION_ID\""
   ```

   Without the injection a replying Codex falls back to matching the git checkout, which refuses when several sessions share it.

5. Self-check: `python3 test_parser.py`, `python3 test_transport.py` and `python3 test_queue.py` in this directory (all load the local `peer-chat.py`, falling back to `~/bin`; the queue tests drive a fake `agtermctl` and never touch live panes).

## Usage

Open a split, start one agent in each pane yourself — the recipe never starts agents, by design. Ask either one to talk to the other ("chat with codex", "work with opencode on this"); the skill fires and the first message goes through the script:

```sh
peer-chat.py --to opencode --defer --stdin <<'CHAT'
the message goes here, as one paragraph
CHAT
```

A deferred send answers with `{"queued": .., "id": "..", ..}` — stored, not delivered or read. The `queued` number counts normalized characters, while `--queue-status <id>` reports `source_bytes` in UTF-8 bytes; both include the sender label added by the script. An overlong OpenCode message is refused before queueing with both counts; write the answer to a readable file and send a short path instead. `--queue-status <id>` reports the outcome; `sent` confirms composer submission, not that the peer read it. A failed delivery includes payload-free `diagnostic` metadata (phase, exception/cause classes, reason, cleanup result and attempt); `cleanup_failed` also includes a fixed, text-free `cleanup_reason` when available. On `failed`, the queue deletes the body: keep the original text until delivery is confirmed, and resolve the cause before retrying. In particular, `failed / cleanup_failed` may leave a partial draft in the composer: do not press Enter; inspect the target pane and manually clear only the fragment belonging to this send before continuing. Historical failures have no such detail. An unrecognizable composer fails after the direct retry budget instead of holding that pane's queue until TTL; busy composers and permission dialogs remain pending. The worker process is spawned on demand and exits once the queue is empty; if it dies mid-send, the affected record becomes `uncertain` and is reported by `--queue-status` instead of being retyped. Without `--defer` the message is delivered synchronously, exactly as before.

`--to` takes any name from the config. Labels (`Chat from Claude: `, `Chat from Codex: `, `Chat from OpenCode: `) are derived from the sending pane's kind and added by the script; any peer self-label (`Chat from opencode-lead: …`) is stripped first. `--queue` (Tab submit) remains Codex-only and composes with `--defer`. `--prepare-message` / `--message-file` work as in the original recipe.

When the peer runs in a full-pane session — no split — the script cannot infer it from the current checkout, so name that session: `agtermctl tree` lists the ids and their titles, and any unambiguous prefix of an id works. `--session <id>` also pins the pane, so a deferred delivery and a direct send to the same pane cannot interleave; `--window <id>` pins the agterm window when a session id is ambiguous across windows.

```sh
peer-chat.py --to opencode --session BEFBFA91-0170-4D3A-AED8-F68B0D55B582 --stdin <<'CHAT'
the message goes here, as one paragraph
CHAT
```

The first exchange in each pair stops on an approval prompt in one of the panes — answer it yourself; agents are never allowed to. With `--defer` the queued message is delivered right after you do.

## How it works

Before typing, the script resolves the session (from `AGTERM_SESSION_ID`, from `--session <id>`, or a single checkout match), finds which pane runs the target command (the split pane, or the primary pane of a full-pane session; opposite of the sender when both match) and, when no pane answers to that command, falls back to the screen and takes the pane that shows a composer of the configured kind. It then confirms the target composer is in a recognized empty state: kind-specific parsers (claude `❯` prompt, codex `›` prompt with placeholder or tip, opencode bordered box with its status row and decorations filtered out). The body is typed as bounded, separately observed events — paced for opencode — with a probe marker that must be confirmed removed before the submit key is sent. Acceptance requires the composer to return to its empty state.

When a chunk cannot be verified, the script withholds the submit, restores the empty composer (exact owned-text backspacing, or bounded blind backspacing for kinds whose pre-write emptiness was proven), and retries the whole send — up to five attempts, each retry with a halved chunk budget. Failures after the submit request stay ambiguous and are never retried.

## Limits

- The script types into another pane's composer. Do not type in the receiving pane during a send.
- An opencode pane that started together with the split may not repaint (blank screen above the status bar — a TUI renderer desync). The send refuses; fix the pane with a geometry nudge: `agtermctl surface zoom show --target surface:<SID>:right; agtermctl surface zoom hide`.
- A fresh opencode session (splash box) or any chooser makes the send refuse. Write something in that pane first; never answer choosers for the user.
- A message that is a lone path (`~/foo`) is taken for opencode's empty-composer cwd hint when the session's cwd is not visible in the capture and the composer shows no input row; avoid such one-token messages.
- OpenCode inline messages over 400 normalized characters including the sender label are refused before typing, for both v1 and v2. Put long content in a readable file and send a short path.
- A target that shows none of the typed text is not reading the keyboard: the send stops after the first chunk and says `the target accepted no input at all` instead of retrying with a smaller chunk. A Claude Code build can stop reading input after spawning ripgrep; only restarting the peer brings the input back.
- No transcript: the conversation lives in the two panes. A reply is never promised.
- Same-kind pairs share one label (`Chat from Claude: ` for claude-zai ↔ claude-minimax); the peer is identified by pane position, not by label.
- The delivery pin is a terminal pane, not a conversation: a peer that starts a new conversation in the same pane is not detected. Cancel queued records (`--queue-cancel`) before switching the topic in the target pane, and rely on the TTL (default 30 minutes) to retire stale ones.
- The queue never answers permission dialogs or waits for a peer's reply — it only waits for a confirmably ready composer. A crashed worker's in-flight record becomes `uncertain` and must be resolved by reading the pane; recovery from a crash happens on the next enqueue, not automatically.
