#!/usr/bin/env python3
"""Fixture-based self-check for the generalized peer-chat parsers and pane rules."""

import importlib.util
import os
import sys
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["PEER_CHAT_CONFIG"] = os.path.join(HERE, "fixtures", "peer-chat.json")
source = os.path.join(HERE, "peer-chat.py")
if not os.path.exists(source):
    source = os.path.join(os.path.expanduser("~/bin"), "peer-chat.py")
spec = importlib.util.spec_from_file_location("peer_chat", source)
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

# the live-modal detector: signal phrase AND choice row together, judged
# structurally so transcript quotes above a live box never block a send
assert pc.opencode_permission_dialog(dialog) is True, (
    "the captured dialog replaces the composer (no bottom border) and must count"
)
bordered_dialog = dialog + (
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert pc.opencode_permission_dialog(bordered_dialog) is True
# a full bordered dialog quote typed INTO the draft is indistinguishable from
# a live modal, so it fails closed: the send refuses and the pane is read
draft_quoting_full_dialog = dialog + (
    "  ┃\n"
    "  ┃  Build · GPT-6 Astra\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert pc.opencode_permission_dialog(draft_quoting_full_dialog) is True
# transcript rendering is unbordered, so a quoted dialog in an old answer sits
# outside the box walk and never blocks a live composer
plain_transcript_quote = (
    "Previous answer: target showed △ Permission required yesterday.\n"
    "\n"
    "  ┃\n"
    "  ┃  Build · Test · high\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert pc.opencode_permission_dialog(plain_transcript_quote) is False
phrase_only_draft = (
    "  ┃  the peer wrote: △ Permission required appeared\n"
    "  ┃\n"
    "  ┃  Build · GPT-6 Astra\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert pc.opencode_permission_dialog(phrase_only_draft) is False, (
    "the phrase without a choice row is a quote, not a modal"
)
# a live dialog stays detected through the modal gate used by composer reads
oc_prof = pc.Profile(
    agent="opencode", command="opencode", label="", submit="\n", pane="left"
)
assert pc.active_modal(oc_prof, dialog) is True

# collapsed busy: transcript row directly above the status row, no input area
collapsed = (
    "                                         Thought: Planning the next step\n"
    "  ┃  Build · GPT-5.6 Luna OpenAI · medium\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert pc.opencode_live_prompt_text(collapsed) == ""
assert not pc.opencode_composer_has_input_area(collapsed)
pure_thinking = (
    "                                         Thought: Planning the next step\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert pc.opencode_live_prompt_text(pure_thinking) is None
# idle empty composer: transcript above, blank input row between it and status
idle_empty = (
    "                                         Thought: Planning the next step\n"
    "  ┃\n"
    "  ┃  Build · GPT-5.6 Luna OpenAI · medium\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert pc.opencode_live_prompt_text(idle_empty) == ""
assert pc.opencode_composer_has_input_area(idle_empty) is True
# draft above the status row: content is recognised
full_box = (
    "  ┃  pending draft text\n"
    "  ┃\n"
    "  ┃  Build · GPT-5.6 Luna OpenAI · medium\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert pc.opencode_live_prompt_text(full_box) == "pending draft text"
assert pc.opencode_composer_has_input_area(full_box) is True
# --- path-like draft rows are draft content, never filtered ---------------
# narrow layout: the draft keeps the blank input row below it
path_draft = (
    "  ┃  ~/notes.txt\n"
    "  ┃\n"
    "  ┃  Build · GPT-5.6 Luna OpenAI · medium\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert pc.opencode_live_prompt_text(path_draft) == "~/notes.txt"
# wide layout: the cwd hint sits under the draft and is dropped, the draft stays
wide_with_hint = (
    "  ┃  ~/notes.txt\n"
    "  ┃  ~/.config/agterm\n"
    "  ┃  Build · GPT-5.6 Luna OpenAI · medium\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert pc.opencode_live_prompt_text(wide_with_hint) == "~/notes.txt"

# --- unflatten: short glued captures and the old border style --------------
glued_old_border = (
    "  │  черновик после сбоя" + " " * 120 + "│  Build · GPT-5.6 Luna · med\n"
    "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
assert pc.opencode_live_prompt_text(glued_old_border) == "черновик после сбоя"

# --- blind recovery refuses to delete foreign text -------------------------
owned = "наш текст в композере"
foreign = owned + " и чужой пользовательский текст"
assert not foreign.replace("\n", "").endswith(owned.replace("\n", ""))
assert owned.replace("\n", "").endswith(owned.replace("\n", ""))

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
claude_screen = (
    "✻ Truncating… (esc to interrupt · 1m 2s)\n"
    "\n"
    "❯ hello there\n"
    "  ──────────\n"
    "  ? for shortcuts"
)
expect("claude prompt is read", pc.claude_live_prompt_text(claude_screen), "hello there")

# claude modal detection: numbered transcript rows coexist with the prompt and
# stay harmless; a chooser with numbered options and no prompt row is a modal
claude_numbered_transcript = (
    "1. The 747/3603 error rate is unchanged since the fix\n"
    "2. Latency improved by 12%\n"
    "❯ \n"
    "  ──────────\n"
    "  ? for shortcuts"
)
assert pc.claude_permission_dialog(claude_numbered_transcript) is False
claude_chooser = (
    "Needs permission to run Bash\n"
    "❯ 1. Yes\n"
    "  2. Yes, and don't ask again this session\n"
    "  3. No (esc)"
)
assert pc.claude_permission_dialog(claude_chooser) is True
cl_prof = pc.Profile(agent="claude", command="claude", label="", submit="\n")
assert pc.active_modal(cl_prof, claude_chooser) is True
assert pc.active_modal(cl_prof, claude_numbered_transcript) is False

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
# A peer in the primary pane of a full-pane session is addressable too: the
# split is not required, only a pane that runs the peer.
assert pc.has_target(info_nosplit, claude_target) is True
expect(
    "full-pane session resolves its primary pane",
    pc.resolve_pane(info_nosplit, claude_target),
    "left",
)

# explicit --pane override verified against candidates
explicit = pc.Profile(
    agent="claude", command="claude", label="", submit="\n", pane="left"
)
info = dict(info_both)
assert "left" in pc.pane_candidates(info, explicit)
assert "right" in pc.pane_candidates(info, explicit)

same_session = {"id": "sid", **info_left}
with patch.dict(os.environ, {"AGTERM_SESSION_ID": "sid", "AGTERM_PANE": "left"}):
    for pane in (None, "left"):
        target = pc.Profile(agent="claude", command="claude", label="", submit="\n", pane=pane)
        with patch.object(pc, "find_node", return_value=same_session):
            try:
                pc.require_target("sid", target)
                raise AssertionError("sending to own pane must refuse")
            except RuntimeError as err:
                assert "sending pane" in str(err)
    other_session = {"id": "other", **info_left}
    assert pc.resolve_pane(other_session, claude_target) == "left"

with patch.dict(os.environ, {"AGTERM_SESSION_ID": "sid", "AGTERM_PANE": "scratch"}), patch.object(
    pc, "find_node", return_value=same_session
):
    try:
        pc.require_target("sid", explicit)
        raise AssertionError("unknown sending pane must refuse")
    except RuntimeError as err:
        assert "sending pane is unknown" in str(err)

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

# --- opencode v2: two-part status row, cwd under the box ----------------------------
v2_split = fixture("opencode-v2-split.txt")
v2_wide = fixture("opencode-v2-wide.txt")
expect("v2 split pane parses to empty", pc.opencode_live_prompt_text(v2_split), "")
expect("v2 wide pane parses to empty", pc.opencode_live_prompt_text(v2_wide), "")
expect(
    "v2 split pane has an input area",
    pc.opencode_composer_has_input_area(v2_split),
    True,
)
expect(
    "v2 wide pane has an input area",
    pc.opencode_composer_has_input_area(v2_wide),
    True,
)
expect("v2 split pane row width", pc.opencode_row_width(v2_split), 107)
expect("v2 wide pane row width", pc.opencode_row_width(v2_wide), 215)

# --- cleanup: what the visible composer text is -------------------------------------
DRAFT = "Chat from Claude: hello"
expect("an empty composer holds nothing", pc.composer_owned_mode("", DRAFT)[0], "empty")
expect("a foreign row is not ours", pc.composer_owned_mode("someone typed", DRAFT)[0], "foreign")
expect("the end of our text is a tail", pc.composer_owned_mode(DRAFT, DRAFT)[0], "tail")
expect("the beginning of our text is a head", pc.composer_owned_mode("Chat from", DRAFT)[0], "head")
expect("the middle of our text is a middle", pc.composer_owned_mode("from Claude:", DRAFT)[0], "middle")

# --- cleanup: what may be erased -----------------------------------------------------
expect(
    "an empty composer needs no erasing",
    pc.composer_cleanup_decision("", DRAFT, True)[0],
    "done",
)
expect(
    "foreign text is never erased",
    pc.composer_cleanup_decision("someone typed", DRAFT, True)[0],
    "refuse",
)
expect(
    "a confirmed tail is erased exactly",
    pc.composer_cleanup_decision(DRAFT, DRAFT, True)[0],
    "delete_exact",
)
decision, count = pc.composer_cleanup_decision("Chat from", DRAFT, True)
expect("a visible head is erased as a whole", decision, "delete_all")
assert count == len(DRAFT), "the whole owned draft goes at once"
expect(
    "a head is not erased blind from a busy composer",
    pc.composer_cleanup_decision("Chat from", DRAFT, False)[0],
    "refuse",
)

# --- the build of the peer and the message length it accepts ------------------------
expect("v1 prints a bare version", pc.opencode_version_major("1.18.29+moonug"), 1)
expect("v2 prints a prefixed version", pc.opencode_version_major("opencode v2.2.1"), 2)
expect("no version at all", pc.opencode_version_major(""), None)
expect("v1 inline message limit", pc.OPENCODE_V1_MESSAGE_LIMIT, 400)
expect("v2 inline message limit", pc.OPENCODE_V2_MESSAGE_LIMIT, 400)

# --- a v2 permission dialog must never be typed into ---------------------------------
v2_dialog = """╭─ Permission required ─
│ △ Permission required
│ ❯ 1. Allow once
│   2. Allow always
│   3. Reject
╰─"""
expect("a v2 permission dialog is recognised", pc.opencode_permission_dialog(v2_dialog), True)
expect(
    "a dialog without a reject option is not one",
    pc.opencode_permission_dialog(v2_dialog.replace("3. Reject", "3. Later")),
    False,
)

# --- a claude build behind a wrapper: the screen names the kind ----------------------
build_capture = fixture("claude-build-empty.txt")
expect(
    "the wrapped build's empty composer is recognised",
    pc.claude_live_prompt_text(build_capture),
    "",
)
expect(
    "the wrapped build is a claude composer",
    pc.kind_on_screen(build_capture),
    "claude",
)
expect(
    "a shell prompt names no kind",
    pc.kind_on_screen("┌─[moonug@moonug-osx] [~/projects]\n└─[√] $ "),
    None,
)
expect(
    "the wrapped build shows a claude composer",
    pc.shows_kind(claude_profile, build_capture),
    True,
)

# A build that draws its own caret block never moves the terminal caret: the
# prompt glyph occupies column 0, so the reported column is 0 and says nothing
# about the composer.
expect("the caret at the prompt column agrees", pc.caret_column_agrees(2), True)
expect("a build that reports no caret agrees", pc.caret_column_agrees(0), True)
expect("a caret past the composer disagrees", pc.caret_column_agrees(5), False)
expect(
    "an empty composer with no reported caret is clear",
    pc.composer_is_clear(claude_profile, ("", 0)),
    True,
)
expect(
    "a draft with no reported caret is not clear",
    pc.composer_is_clear(claude_profile, ("hello", 0)),
    False,
)

# A peer that runs through a wrapper answers to no configured command, so the
# screen is what identifies it.
wrapped_session = {
    "id": "wrapped",
    "hasSplit": True,
    "foreground": ["opencode"],
    "splitForeground": ["bun", "/projects/claude-code-build/dist/cli.js"],
}
shell_session = {
    "id": "shell",
    "hasSplit": True,
    "foreground": ["opencode"],
    "splitForeground": ["zsh"],
}


def pane_reader(text):
    def read(sid, profile, window=None):
        return text if profile.pane == "right" else ""

    return read


with patch.object(pc, "_pane_text_unchecked", pane_reader(build_capture)):
    expect(
        "a wrapped peer is found by its composer",
        pc.pane_candidates(wrapped_session, claude_target),
        ["right"],
    )
with patch.object(
    pc, "_pane_text_unchecked", pane_reader("┌─[moonug@moonug-osx]\n└─[√] $ ")
):
    expect(
        "a shell pane is not a peer",
        pc.pane_candidates(shell_session, claude_target),
        [],
    )
expect(
    "the pane running another agent is never a peer",
    pc.pane_candidates(
        {"hasSplit": True, "foreground": ["opencode"], "splitForeground": ["opencode"]},
        claude_target,
    ),
    [],
)

print("all parser and pane-rule checks passed")
