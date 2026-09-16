# Configurable peer chat

Let any two configured coding agents hold a conversation with each other in one agterm split. A generalization of [two-agent-chat](../two-agent-chat): instead of two hardcoded profiles (Claude Code left, Codex right), agents come from a JSON config — `claude`, `claude-zai`, `claude-minimax`, `codex`, `opencode`, or anything you add — each with a composer kind that tells the transport how to read its UI.

## What it does

Two agents run side by side in a split, one per pane, and talk to each other. Each side sends messages through `peer-chat.py`, which verifies the target pane's composer before typing, sends the body as bounded observed chunks, and confirms submission. You watch both halves; your part is naming the peer ("work with codex") and stepping in when a decision is yours.

What this fork adds over the original recipe:

- **Config-driven agents.** `~/.config/agterm/peer-chat.json` maps a name to a composer kind (`claude`, `codex`, `opencode`) and the process name agterm should look for. Wrapper scripts and zsh functions that exec the same binary (claude-zai/claude-minimax all show `claude`) work out of the box.
- **Any pane layout.** The original pins Claude left and Codex right. Here the target pane is resolved live: the split pane whose foreground runs the peer's command; when both panes run the same command, the pane opposite to the sender (via `AGTERM_PANE`), with `--pane left|right` as an explicit override.
- **opencode support.** A composer parser for opencode's boxed TUI (light/heavy borders, splash and conversation states, status rows, cursor-glyph and cwd decorations). Typing into a redrawing opencode is paced in small slices because single large writes occasionally drop a character; verification catches any loss and refuses to submit.
- **Recovery.** When verification fails, cleanup restores the empty composer even if the typed text got corrupted (bounded blind backspacing, proven safe because the pre-write composer was confirmed empty), and the send retries — but only after cleanup is confirmed.
- **Robust long messages.** The suffix check stops as soon as the expected text is fully consumed by the rows below, so drafts taller than the composer (internal scroll) and busy targets with live tool rows above the composer verify correctly.

## Requirements

- agterm 0.24.0 or later (`surface cursor`). The script refuses to type without it.
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

   `kind` picks the composer parser; `command` is the name matched against agterm's view of the pane's foreground process. A wrapper or zsh function that ends up running `claude` is still matched by `command: "claude"` — the agent name is then mostly documentation.
3. Install the skills: `SKILL-claude.md` → `~/.claude/skills/peer-chat/SKILL.md` (covers claude and every wrapper flavour), `SKILL-codex.md` → `~/.codex/skills/peer-chat/SKILL.md`, `SKILL-opencode.md` → `~/.config/opencode/skills/peer-chat/SKILL.md`. Each loader requires the installed file to be named exactly `SKILL.md`.
4. For Codex, add the two `peer-chat.py` prefix rules from [two-agent-chat's setup](../two-agent-chat) to `~/.codex/rules/default.rules` (one per `--to <name>` you plan to use, plus `--prepare-message`), and start Codex with its pane id injected:

   ```sh
   codex -c "shell_environment_policy.set.AGTERM_SESSION_ID=\"$AGTERM_SESSION_ID\""
   ```

   Without the injection a replying Codex falls back to matching the git checkout, which refuses when several sessions share it.

5. Self-check: `python3 test_parser.py` in this directory (loads the local `peer-chat.py`, falls back to `~/bin`).

## Usage

Open a split, start one agent in each pane yourself — the recipe never starts agents, by design. Ask either one to talk to the other ("chat with codex", "work with opencode on this"); the skill fires and the first message goes through the script:

```sh
peer-chat.py --to opencode --stdin <<'CHAT'
the message goes here, as one paragraph
CHAT
```

`--to` takes any name from the config. Labels (`Chat from Claude: `, `Chat from Codex: `, `Chat from OpenCode: `) are derived from the sending pane's kind and added by the script; any peer self-label (`Chat from opencode-lead: …`) is stripped first. `--queue` (Tab submit) remains Codex-only. `--prepare-message` / `--message-file` work as in the original recipe.

The first exchange in each pair stops on an approval prompt in one of the panes — answer it yourself; agents are never allowed to.

## How it works

Before typing, the script resolves the session (from `AGTERM_SESSION_ID`, or a single checkout match), finds which split pane runs the target command (opposite of the sender when both match), and confirms the target composer is in a recognized empty state: kind-specific parsers (claude `❯` prompt, codex `›` prompt with placeholder or tip, opencode bordered box with its status row and decorations filtered out). The body is typed as bounded, separately observed events — paced for opencode — with a probe marker that must be confirmed removed before the submit key is sent. Acceptance requires the composer to return to its empty state.

When a chunk cannot be verified, the script withholds the submit, restores the empty composer (exact owned-text backspacing, or bounded blind backspacing for kinds whose pre-write emptiness was proven), and retries the whole send — up to five attempts. Failures after the submit request stay ambiguous and are never retried.

## Limits

- The script types into another pane's composer. Do not type in the receiving pane during a send.
- An opencode pane that started together with the split may not repaint (blank screen above the status bar — a TUI renderer desync). The send refuses; fix the pane with a geometry nudge: `agtermctl surface zoom show --target surface:<SID>:right; agtermctl surface zoom hide`.
- A fresh opencode session (splash box) or any chooser makes the send refuse. Write something in that pane first; never answer choosers for the user.
- A message that is a lone path (`~/foo`) is indistinguishable from opencode's empty-composer cwd hint and gets filtered; avoid such one-token messages.
- No transcript: the conversation lives in the two panes. A reply is never promised.
- Same-kind pairs share one label (`Chat from Claude: ` for claude-zai ↔ claude-minimax); the peer is identified by pane position, not by label.
