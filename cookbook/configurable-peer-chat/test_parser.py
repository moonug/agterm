#!/usr/bin/env python3
"""Fixture-based self-check for the generalized peer-chat parsers and pane rules."""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "peer_chat", os.path.join(os.path.expanduser("~/bin"), "peer-chat.py")
)
pc = importlib.util.module_from_spec(spec)
sys.modules["peer_chat"] = pc
spec.loader.exec_module(pc)


def fixture(name: str) -> str:
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return fh.read()


def expect(label, actual, wanted):
    assert actual == wanted, f"{label}: got {actual!r}, want {wanted!r}"


# --- opencode parser: recognized states -------------------------------------------------
expect(
    "splash empty parses to empty",
    pc.opencode_live_prompt_text(fixture("opencode-splash-empty.txt")),
    "",
)
expect(
    "splash clean parses to empty",
    pc.opencode_live_prompt_text(fixture("opencode-splash-clean.txt")),
    "",
)
expect(
    "splash text is read back",
    pc.opencode_live_prompt_text(fixture("opencode-splash-text.txt")),
    "hello from peer fixture, this is a reasonably long message to force\n"
    "wrapping across multiple rows of the composer box",
)
expect(
    "conversation empty (busy, heavy border) parses to empty",
    pc.opencode_live_prompt_text(fixture("opencode-conv-empty.txt")),
    "",
)
expect(
    "conversation clean parses to empty",
    pc.opencode_live_prompt_text(fixture("opencode-conv-clean.txt")),
    "",
)
expect(
    "conversation text is read back",
    pc.opencode_live_prompt_text(fixture("opencode-conv-text.txt")),
    "hello from peer fixture, this is a reasonably long message to force wrapping "
    "across multiple rows",
)
expect(
    "old-style empty box (cursor glyph + transcript above) parses to empty",
    pc.opencode_live_prompt_text(fixture("opencode-oldstyle-empty.txt")),
    "",
)
parsed_long = pc.opencode_live_prompt_text(fixture("opencode-long-draft.txt"))
assert parsed_long is not None, "long clipped draft must parse"
assert parsed_long.startswith("из discovery"), "top of the draft stays clipped"
assert "Chat from OpenCode:" not in parsed_long, "label scrolled away"
assert parsed_long.count("\n") >= 15, "long draft spans many rows"

# --- composer_has_expected_tail: early stop above a consumed suffix ------
expected = "alpha beta gamma delta epsilon zeta"
# the top row is foreign (a busy target's live tool row) yet every row below
# it already consumes the whole expected suffix
content = "JUNK live tool row\nalpha beta\ngamma delta\nepsilon zeta"
assert pc.composer_has_expected_tail(content, expected, expected) is True
# a junk row BETWEEN wrapped rows of the suffix still fails
content_bad = "alpha beta\nJUNK live row\ngamma delta\nepsilon zeta"
assert pc.composer_has_expected_tail(content_bad, expected, expected) is False

# --- pending permission dialog and collapsed composer -------------------
dialog = fixture("opencode-permission-dialog.txt")
assert pc.OPENCODE_DIALOG_RE.search(dialog) is not None
assert pc.opencode_live_prompt_text(dialog) is None

collapsed = (
    "                                         Thought: Planning the next step\n"
    "  ┃  Build · GPT-5.6 Luna OpenAI · medium\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert pc.opencode_live_prompt_text(collapsed) is None
assert not pc.opencode_input_block_present(collapsed)
pure_thinking = (
    "                                         Thought: Planning the next step\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert not pc.opencode_input_block_present(pure_thinking)
full_box = (
    "  ┃  pending draft text\n"
    "  ┃\n"
    "  ┃  Build · GPT-5.6 Luna OpenAI · medium\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert pc.opencode_input_block_present(full_box) is True
# --- normalize strips foreign self-labels --------------------------------
prof = pc.target_profile("codex", None, False, None)
expect(
    "normalize strips a foreign role label",
    pc.normalize(prof, "Chat from opencode-lead: пункт восемь"),
    prof.label + "пункт восемь",
)
expect(
    "normalize strips its own label too",
    pc.normalize(prof, f"{prof.label}уже подписано"),
    prof.label + "уже подписано",
)

# --- opencode parser: refusals ----------------------------------------------------------
expect("plain shell text is refused", pc.opencode_live_prompt_text("zsh-5.9$ ls\n"), None)
expect("empty screen is refused", pc.opencode_live_prompt_text(""), None)

# --- claude parser smoke (upstream logic, untouched) ------------------------------------
claude_screen = "\n".join(
    [
        "✻ Truncating… (esc to interrupt · 1m 2s)",
        "",
        "❯ hello there",
        "  ──────────",
        "  ? for shortcuts",
    ]
)
expect("claude prompt is read", pc.claude_live_prompt_text(claude_screen), "hello there")

claude_profile = pc.Profile(agent="claude", command="claude", label="", submit="\n")
assert pc.composer_is_empty(claude_profile, "") is True
assert pc.composer_is_empty(claude_profile, "draft text") is False

# --- composer_is_empty dispatch ---------------------------------------------------------
opencode_profile = pc.Profile(
    agent="opencode", command="opencode", label="", submit="\n"
)
assert pc.composer_is_empty(opencode_profile, "") is True
assert pc.composer_is_empty(opencode_profile, "text") is False
codex_profile = pc.Profile(agent="codex", command="codex", label="", submit="\n")
assert pc.composer_is_empty(codex_profile, pc.CODEX_EMPTY_PROMPT) is True

# --- composer_is_clear (cursor gate per kind) -------------------------------------------
assert pc.composer_is_clear(claude_profile, ("", 2)) is True
assert pc.composer_is_clear(claude_profile, ("", 5)) is False
assert pc.composer_is_clear(opencode_profile, ("", 77)) is True
assert pc.composer_is_clear(opencode_profile, ("text", 6)) is False
assert pc.composer_is_clear(claude_profile, None) is False

# --- pane candidates and resolution -----------------------------------------------------
info_both = {
    "hasSplit": True,
    "foreground": ["claude"],
    "splitForeground": ["claude"],
}
info_left = {"hasSplit": True, "foreground": ["claude"], "splitForeground": ["zsh"]}
info_right = {"hasSplit": True, "foreground": ["zsh"], "splitForeground": ["codex"]}
info_none = {"hasSplit": True, "foreground": ["zsh"], "splitForeground": ["zsh"]}
info_nosplit = {"hasSplit": False, "foreground": ["claude"]}

claude_target = pc.Profile(
    agent="claude", command="claude", label="", submit="\n", pane=None
)
codex_target = pc.Profile(
    agent="codex", command="codex", label="", submit="\n", pane=None
)

expect("left-only resolves left", pc.resolve_pane(info_left, claude_target), "left")
expect("right-only resolves right", pc.resolve_pane(info_right, codex_target), "right")

os.environ["AGTERM_PANE"] = "left"
expect("both panes: peer of left is right", pc.resolve_pane(info_both, claude_target), "right")
os.environ["AGTERM_PANE"] = "right"
expect("both panes: peer of right is left", pc.resolve_pane(info_both, claude_target), "left")
os.environ["AGTERM_PANE"] = "scratch"
try:
    pc.resolve_pane(info_both, claude_target)
    raise AssertionError("ambiguous both-panes with unknown sender must refuse")
except RuntimeError:
    pass
os.environ.pop("AGTERM_PANE")
try:
    pc.resolve_pane(info_both, claude_target)
    raise AssertionError("ambiguous both-panes without sender must refuse")
except RuntimeError:
    pass
try:
    pc.resolve_pane(info_none, claude_target)
    raise AssertionError("missing target must refuse")
except RuntimeError:
    pass

assert pc.has_target(info_left, claude_target) is True
assert pc.has_target(info_nosplit, claude_target) is False

# explicit --pane override verified against candidates
explicit = pc.Profile(
    agent="claude", command="claude", label="", submit="\n", pane="left"
)
info = dict(info_both)
assert "left" in pc.pane_candidates(info, explicit)
assert "right" in pc.pane_candidates(info, explicit)

# --- config loading ---------------------------------------------------------------------
agents = pc.load_agents()
for name in ("claude", "claude-zai", "claude-minimax", "codex", "opencode"):
    assert name in agents, f"config misses {name}"
assert agents["claude-zai"]["kind"] == "claude"

profile = pc.target_profile("claude-zai", None, False, None)
assert profile.agent == "claude" and profile.command == "claude"
assert profile.label == "Chat from Claude: "  # sender fallback without agterm env
assert profile.submit == "\n"

try:
    pc.target_profile("nope", None, False, None)
    raise AssertionError("unknown agent must refuse")
except RuntimeError as err:
    assert "unknown peer-chat agent" in str(err)

codex_profile = pc.target_profile("codex", None, True, None)
assert codex_profile.submit == "\t"
opencode_profile = pc.target_profile("opencode", None, False, "left")
assert opencode_profile.pane == "left"

# --- normalize with sender label --------------------------------------------------------
os.environ.pop("AGTERM_SESSION_ID", None)
os.environ.pop("AGTERM_PANE", None)
expect(
    "normalize prepends label",
    pc.normalize(pc.target_profile("codex", None, False, None), "one paragraph"),
    "Chat from Claude: one paragraph",
)
expect(
    "normalize strips a doubled label",
    pc.normalize(
        pc.target_profile("codex", None, False, None),
        "Chat from Claude: already labelled",
    ),
    "Chat from Claude: already labelled",
)
try:
    pc.normalize(pc.target_profile("codex", None, False, None), "")
    raise AssertionError("empty message must refuse")
except ValueError:
    pass

print("all parser and pane-rule checks passed")
