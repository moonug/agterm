#!/usr/bin/env python3
"""Send one peer-chat message between Claude Code and Codex in an agterm split."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid as uuid_module
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

# Live reproduction accepted 1,627 bytes and began losing 1,022-byte windows at
# 1,663 bytes. A narrow Claude composer also scrolls the opening of a 512-byte
# event out of its visible box, so keep each independently observed event small
# enough to remain readable by the post-write check.
TYPE_CHUNK_BYTES = 197
CHUNK_VERIFY_OVERLAP = 32
CHUNK_SETTLE_DELAY = 0.1
COMPOSER_SETTLE_DELAY = 0.5
PROBE_TIMEOUT = 6.0
PROBE_INTERVAL = 0.05
RETRY_ATTEMPTS = 5
RETRY_DELAY = 10.0
BOX_LINES = 80
EMPTY_CURSOR_COLUMN = 2
MAX_MESSAGE_BYTES = 64 * 1024
# Claude can put a short context label inside the top composer rule, for example `e2e`.
RULE_RE = re.compile(r"^\s*[─\u2014-]{10,}(?:\s+[^─\u2014-].*?\s+[─\u2014-]+)?\s*$")
# Codex draws the composer prompt as `›`; with reasoning effort set to ultra it upgrades
# the glyph to `»` (codex-rs/tui/src/bottom_pane/effort_ignition.rs). Both prompt patterns
# are anchored at column zero so prompt-shaped output cannot be mistaken for live input.
# Shell mode shows `!` and is deliberately not matched, so nothing is ever typed there.
CODEX_PROMPT_RE = re.compile(r"^[›»][\s \u2800-\u28FF]?[\s ]*(.*?)\s*$")
CODEX_SHELL_PROMPT_RE = re.compile(r"^![\s ]*(.*?)\s*$")
CODEX_CHOICE_RE = re.compile(r"^\d+\.\s")
# Codex sprays an idle animation of braille particles (U+2800-U+28FF) across the composer
# box, prompt row included, and a single particle can sit in one cell for seconds, so no
# burst of reads sees the text underneath. For the Codex pane only: rows that hold nothing
# but particles count as blank, so they neither hide the prompt row nor glue a notice to
# it; the cell after the prompt marker and the two-cell indent of wrapped rows are
# decoration; everywhere else a particle stands for the one cell it covers, or for an
# empty cell when it trails the text (row_matches, trailing_particle_variants), and the
# empty placeholder is read the same way. A particle can hide a wrong character until it
# moves, which is why send() verifies the whole body again just before Return. Claude's
# composer shows no animation and is compared verbatim.
CODEX_PARTICLE_RE = re.compile(r"[\u2800-\u28FF]")
CODEX_INDENT_RE = re.compile(r"^[\s\u2800-\u28FF]{0,2}")
CODEX_EMPTY_PROMPT = "Ask Codex to do anything"
# Newer codex builds show a rotating tip instead of the fixed placeholder, for
# example `› Use /skills to list available skills`.
CODEX_TIP_RE = re.compile(r"^Use /\S+ .+$")
# Codex prefixes footer rows with two spaces. Only the final row is stripped: a
# multi-row shortcut overlay is indistinguishable from indented modal choices and
# therefore fails closed instead of weakening the live-prompt guard.
CODEX_FOOTER_RE = re.compile(r"^ {2}\S.*$")
CLAUDE_PROMPT_RE = re.compile(r"^\s*❯[\s ]*(.*?)\s*$")
# status-line commands can add padding beyond Claude Code's two-space indent.
CLAUDE_FOOTER_RE = re.compile(r"^ {2,}\S.*$")
CLAUDE_EMPTY_PROMPTS = {
    "",
    "Press up to edit queued messages",
    "Press up to edit queued messages, Enter to send them immediately",
}
# a freshly started Claude Code draws a suggestion in its empty composer. The suggested text is built
# from the user's own frequently-edited files, so only this wrapper is fixed and no literal set can
# cover it. `.+` rather than `[^"]*` because git quotes and escapes a path containing a double quote,
# which then reaches the suggestion with its own quotes intact.
CLAUDE_STARTUP_HINT_RE = re.compile(r'^Try ".+"$')
# opencode draws its composer as a bordered block: a left border (`│` in older
# builds, `┃` in newer ones), a status row (`Build · Model · effort`) directly
# above the bottom border (`╰──╯` or `╹▀▀▀`), and the input rows between one
# blank separator row and the status row. Idle empty composers may add a lone
# cursor glyph (`*`) and a right-aligned cwd hint inside the box; a fresh
# session shows a rotated `Ask anything…` placeholder instead of input rows.
OPENCODE_BORDER_RE = re.compile(r"^\s*[│┃]")
OPENCODE_BOTTOM_RE = re.compile(r"^\s*[╹╰┗][▀─]*\s*╯?", re.MULTILINE)
OPENCODE_STATUS_RE = re.compile(r"^\S+ · .+ · \S+$", re.MULTILINE)
OPENCODE_PLACEHOLDER_RE = re.compile(r'^Ask (?:anything|Codex)[….]?( "[^"]+")?$')
# opencode renders the session cwd as a bare path row separated from the
# status row by blank rows. A user draft never has that blank gap, so the
# hint is dropped only in that exact position.
OPENCODE_PATH_HINT_RE = re.compile(r"^[~/]\S*$")
OPENCODE_GLYPH_ROWS = {"*"}
# A pending permission dialog replaces the input area entirely; typing into it
# (or pressing its confirm key) would answer for the user. The bare phrase is
# only a signal: the live modal is confirmed by its choice row inside the same
# composer block (see opencode_permission_dialog), so a transcript quote of an
# old dialog above a live input area does not block sends.
OPENCODE_DIALOG_RE = re.compile(r"△ Permission required|Allow once\s+Allow always")
OPENCODE_DIALOG_SCAN_ROWS = 24
# While the agent is thinking, the input area and status row collapse and only
# the bottom border remains above the footer. The state is transient.
OPENCODE_TYPE_SLICE = 24
OPENCODE_TYPE_PAUSE = 0.08
MESSAGE_NAME_RE = re.compile(r"peer-chat-[a-z0-9][a-z0-9-]{2,48}\.txt")
MESSAGE_SPOOL = Path(tempfile.gettempdir()) / f"agterm-peer-chat-{os.getuid()}"


KNOWN_KINDS = ("claude", "codex", "opencode")
KIND_NAMES = {"claude": "Claude", "codex": "Codex", "opencode": "OpenCode"}
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "agterm" / "peer-chat.json"


@dataclass
class Profile:
    """One configured agent. `agent` is the composer kind; `pane` is resolved
    against the live tree by require_target and None until then.

    The pin fields bind deferred delivery to the exact destination observed
    when the message was queued: the owning window and session, the pane side
    with its stable pane-id token, the control socket path and its filesystem
    fingerprint, and the agtermctl binary to drive them with. A synchronous
    send leaves them None and resolves everything live, as before."""

    agent: str
    command: str
    label: str
    submit: str
    pane: str | None = None
    window: str | None = None
    session: str | None = None
    pane_id: str | None = None
    socket: str | None = None
    socket_fingerprint: tuple[int, int, int] | None = None
    agtermctl: str | None = None


def load_agents() -> dict[str, dict[str, str]]:
    path = Path(os.environ.get("PEER_CHAT_CONFIG") or DEFAULT_CONFIG_PATH)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise RuntimeError(f"peer-chat config not found: {path}") from err
    agents: dict[str, dict[str, str]] = {}
    for name, spec in (data.get("agents") or {}).items():
        kind = str((spec or {}).get("kind", "")).strip()
        command = str((spec or {}).get("command", "")).strip()
        if kind not in KNOWN_KINDS or not command:
            raise RuntimeError(
                f"peer-chat config {path}: agent {name!r} needs a command and "
                f"kind in {', '.join(KNOWN_KINDS)}"
            )
        agents[name] = {"kind": kind, "command": command_name(command)}
    if not agents:
        raise RuntimeError(f"peer-chat config {path}: no agents defined")
    return agents


@dataclass
class BodyProgress:
    """Exact composer text owned at the latest body transaction boundary."""

    owned_text: str = ""


@dataclass
class DeliveryProgress:
    """Conservative delivery state shared with the outer CLI boundary."""

    phase: str = "not_started"


PROFILES_REMOVED = None


class PromptBlocked(RuntimeError):
    """The target prompt is occupied before any text was written.

    `reason` is the machine-readable cause for callers that must distinguish
    states (a permission dialog is final, a transient redraw is retryable);
    the message text stays human-oriented.
    """

    def __init__(self, message: str, reason: str = "blocked") -> None:
        super().__init__(message)
        self.reason = reason


class ComposerDirty(RuntimeError):
    """A body write started but could not be verified before submission.

    `cleared` records that guarded cleanup provably restored the confirmed
    empty pre-write state, so a caller may retry safely without parsing the
    message text.
    """

    def __init__(
        self,
        message: str,
        owned_text: str,
        interrupted: bool = False,
        cleared: bool = False,
    ) -> None:
        super().__init__(message)
        self.owned_text = owned_text
        self.interrupted = interrupted
        self.cleared = cleared


class DeliveryAmbiguous(RuntimeError):
    """Submission may have started, so the caller must not resend."""


# PromptBlocked reasons that are final for any retry loop: the destination a
# send was pinned to no longer exists in the form it was pinned to.
IDENTITY_BLOCK_REASONS = frozenset(
    {"socket_gone", "socket_replaced", "pane_gone", "pane_changed", "pane_replaced"}
)


def ctl(*args: str, input_text: str | None = None) -> str:
    command = os.environ.get("AGTERMCTL", "agtermctl")
    call = [command, *args]
    # A queue worker must reach the exact server its records were pinned to,
    # not whatever instance the default rendezvous finds later; the pinning
    # environment is set by the worker itself, never inherited from a sender.
    pinned_socket = os.environ.get("PEER_CHAT_SOCKET")
    if pinned_socket:
        call += ["--socket", pinned_socket]
    result = subprocess.run(
        call,
        capture_output=True,
        check=False,
        input=input_text,
        text=True,
        timeout=30,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"agtermctl {' '.join(args)} failed: {detail}")
    return result.stdout


def window_option(window: str | None) -> tuple[str, ...]:
    return ("--window", window) if window else ()


def configured_selector(
    explicit: str | None,
    environment: str,
    label: str,
    default: str | None = None,
) -> str | None:
    """Resolve one explicit or environment selector without blank fallback."""
    raw = explicit if explicit is not None else os.environ.get(environment)
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        raise RuntimeError(f"{label} selector is empty")
    return value


def tree(window: str | None = None) -> Any:
    return json.loads(ctl("tree", "--json", *window_option(window)))


def resolve_window(explicit: str | None) -> str:
    target = configured_selector(
        explicit, "AGTERM_WINDOW_ID", "window", "active"
    )
    assert target is not None
    payload = json.loads(ctl("window", "list", "--json"))
    windows = payload.get("result", {}).get("windows", [])
    if target.lower() == "active":
        matches = [item for item in windows if item.get("active")]
    else:
        exact = [
            item
            for item in windows
            if str(item.get("id", "")).lower() == target.lower()
        ]
        matches = exact or [
            item
            for item in windows
            if str(item.get("id", "")).lower().startswith(target.lower())
        ]
    if len(matches) != 1 or not matches[0].get("id"):
        detail = "ambiguous" if len(matches) > 1 else "not found"
        raise RuntimeError(f"agterm window {target!r} is {detail}")
    if not matches[0].get("open"):
        raise RuntimeError(f"agterm window {target!r} is not open")
    return str(matches[0]["id"])


def open_window_ids() -> list[str]:
    """Return every open window id for an explicit-session lookup."""
    payload = json.loads(ctl("window", "list", "--json"))
    return [
        str(item["id"])
        for item in payload.get("result", {}).get("windows", [])
        if item.get("open") and item.get("id")
    ]


def checkout_key(path: str) -> str:
    command = [
        "git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return os.path.realpath(path)
    if result.returncode == 0 and result.stdout.strip():
        return os.path.realpath(result.stdout.strip())
    return os.path.realpath(path)


def walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if "id" in value and ("foreground" in value or "splitForeground" in value):
            yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def command_name(value: str) -> str:
    name = os.path.basename(value.strip())
    if not name or name in {".", ".."} or any(char.isspace() for char in name):
        raise ValueError("target command must be one executable name or path")
    return name


def sender_label() -> str:
    """Derive the message label from the sending pane's own agent kind."""
    sid = os.environ.get("AGTERM_SESSION_ID")
    pane = os.environ.get("AGTERM_PANE")
    if sid and pane in ("left", "right"):
        field = "foreground" if pane == "left" else "splitForeground"
        for window in open_window_ids():
            try:
                info = find_node(sid, window)
            except RuntimeError:
                continue
            foreground = info.get(field) or []
            for spec in load_agents().values():
                if runs(foreground, spec["command"]):
                    return f"Chat from {KIND_NAMES[spec['kind']]}: "
    return "Chat from Claude: "


def target_profile(
    target: str,
    explicit_command: str | None,
    queue: bool = False,
    explicit_pane: str | None = None,
) -> Profile:
    agents = load_agents()
    spec = agents.get(target)
    if spec is None:
        raise RuntimeError(
            f"unknown peer-chat agent {target!r}; configured: "
            f"{', '.join(sorted(agents))}"
        )
    env_name = f"PEER_CHAT_{spec['kind'].upper()}_COMMAND"
    configured = explicit_command or os.environ.get(env_name) or spec["command"]
    return Profile(
        agent=spec["kind"],
        command=command_name(configured),
        label=sender_label(),
        submit="\t" if queue else "\n",
        pane=explicit_pane,
    )


def runs(foreground: Any, command: str) -> bool:
    if not isinstance(foreground, list):
        return False
    pattern = re.compile(rf"(?:^|[/\s]){re.escape(command)}(?:$|\s)")
    return any(pattern.search(str(part)) for part in foreground)


def find_node(sid: str, window: str | None = None) -> dict[str, Any]:
    needle = sid.lower()
    matches = [
        info
        for info in walk(tree(window))
        if str(info.get("id", "")).lower().startswith(needle)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError(f"no such session: {sid}")
    raise RuntimeError(f"ambiguous session prefix {sid!r}")


def pane_candidates(info: dict[str, Any], profile: Profile) -> list[str]:
    candidates = []
    if runs(info.get("foreground"), profile.command):
        candidates.append("left")
    if runs(info.get("splitForeground"), profile.command):
        candidates.append("right")
    return candidates


def has_target(info: dict[str, Any], profile: Profile) -> bool:
    """Any split pane of this session runs the target command."""
    if not info.get("hasSplit"):
        return False
    return bool(pane_candidates(info, profile))


def resolve_pane(info: dict[str, Any], profile: Profile) -> str:
    """Pick which split pane runs the target command."""
    candidates = pane_candidates(info, profile)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) == 2:
        sender = os.environ.get("AGTERM_PANE")
        if sender == "left" or sender == "right":
            return "right" if sender == "left" else "left"
        raise RuntimeError(
            f"both split panes run {profile.command!r} and the sending pane is "
            "unknown; pass --pane left|right"
        )
    raise RuntimeError(
        f"{profile.agent} target pane is not running {profile.command!r}; "
        "for a wrapper, pass --target-command NAME"
    )


def require_target(
    sid: str, profile: Profile, window: str | None = None
) -> str:
    info = find_node(sid, pinned_window(profile, window))
    if not info.get("hasSplit"):
        raise RuntimeError(f"session {sid} has no split")
    if profile.pane is None:
        profile.pane = resolve_pane(info, profile)
    elif profile.pane not in pane_candidates(info, profile):
        raise RuntimeError(
            f"{profile.agent} pane {profile.pane} is not running "
            f"{profile.command!r}; for a wrapper, pass --target-command NAME"
        )
    return str(info["id"])


def resolve_session(
    explicit: str | None, profile: Profile, window: str | None = None
) -> str:
    sid = configured_selector(explicit, "AGTERM_SESSION_ID", "session")
    if sid:
        return require_target(sid, profile, window)
    wanted = checkout_key(os.getcwd())
    matches = [
        str(info["id"])
        for info in walk(tree(window))
        if has_target(info, profile)
        and info.get("cwd")
        and checkout_key(str(info["cwd"])) == wanted
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError(
            "no session in this checkout runs "
            f"{profile.command!r} in a split; for a wrapper, pass --target-command NAME"
        )
    raise RuntimeError(
        "more than one session shares this checkout; pass --session ID or launch "
        "Codex with shell_environment_policy.set.AGTERM_SESSION_ID"
    )


def resolve_target(
    explicit_session: str | None,
    explicit_window: str | None,
    profile: Profile,
) -> tuple[str, str]:
    """Resolve one session and pin its owning window before any pane read."""
    session = configured_selector(
        explicit_session, "AGTERM_SESSION_ID", "session"
    )
    window_selector = (
        configured_selector(explicit_window, "AGTERM_WINDOW_ID", "window")
        if explicit_window is not None or explicit_session is None
        else None
    )
    if window_selector is not None:
        window = resolve_window(window_selector)
        return window, resolve_session(explicit_session, profile, window)
    if not session:
        window = resolve_window(None)
        return window, resolve_session(None, profile, window)

    needle = session.lower()
    matches: list[tuple[str, dict[str, Any]]] = []
    for window in open_window_ids():
        matches.extend(
            (window, info)
            for info in walk(tree(window))
            if str(info.get("id", "")).lower().startswith(needle)
        )
    if len(matches) > 1:
        raise RuntimeError(f"ambiguous session prefix {session!r}")
    if not matches:
        raise RuntimeError(f"no such session: {session}")
    window, info = matches[0]
    sid = str(info["id"])
    if not info.get("hasSplit"):
        raise RuntimeError(f"session {sid} has no split")
    if not has_target(info, profile):
        raise RuntimeError(
            f"{profile.agent} target pane is not running {profile.command!r}; "
            "for a wrapper, pass --target-command NAME"
        )
    return window, sid


def pinned_window(profile: Profile, window: str | None) -> str | None:
    """The explicit window, or the pinned one for a deferred delivery."""
    return window if window is not None else profile.window


def socket_fingerprint(path: str) -> tuple[int, int, int]:
    """Filesystem identity of the control socket: device, inode, ctime."""
    info = os.stat(path)
    return (info.st_dev, info.st_ino, info.st_ctime_ns)


def verify_pinned_identity(
    sid: str, profile: Profile, window: str | None = None
) -> None:
    """Re-check a pinned destination right before a typed event.

    Raises PromptBlocked when the socket was replaced, the session or split
    is gone, the pane side changed, or the pane-id token no longer matches.
    A deferred send must land in the pane that was observed when the message
    was queued — never in whatever later occupies the same side.
    """
    if profile.pane_id is None and profile.socket_fingerprint is None:
        return
    if profile.socket_fingerprint is not None and profile.socket:
        try:
            current = socket_fingerprint(profile.socket)
        except OSError as err:
            raise PromptBlocked(
                "target control socket is gone; delivery cancelled",
                "socket_gone",
            ) from err
        if current != profile.socket_fingerprint:
            raise PromptBlocked(
                "target control socket was replaced (agterm restarted); "
                "delivery cancelled",
                "socket_replaced",
            )
    info = find_node(sid, pinned_window(profile, window))
    if not info.get("hasSplit"):
        raise PromptBlocked(
            "target split no longer exists; delivery cancelled", "pane_gone"
        )
    pane = profile.pane
    if pane not in pane_candidates(info, profile):
        raise PromptBlocked(
            f"target pane {pane} no longer runs {profile.command!r}; "
            "delivery cancelled",
            "pane_changed",
        )
    token = info.get("paneID") if pane == "left" else info.get("splitPaneID")
    if profile.pane_id and token != profile.pane_id:
        raise PromptBlocked(
            "target pane was replaced (pane-id changed); delivery cancelled",
            "pane_replaced",
        )


def pane_text(
    sid: str, profile: Profile, window: str | None = None
) -> str:
    require_target(sid, profile, window)
    return _pane_text_unchecked(sid, profile, window)


def _pane_text_unchecked(
    sid: str, profile: Profile, window: str | None = None
) -> str:
    return ctl(
        "session",
        "text",
        "--pane",
        profile.pane,
        "--target",
        sid,
        "--lines",
        str(BOX_LINES),
        *window_option(pinned_window(profile, window)),
    )




def cursor_column(
    sid: str, profile: Profile, window: str | None = None
) -> int:
    require_target(sid, profile, window)
    return _cursor_column_unchecked(sid, profile, window)


def _cursor_column_unchecked(
    sid: str, profile: Profile, window: str | None = None
) -> int:
    value = ctl(
        "surface",
        "cursor",
        "--target",
        f"surface:{sid}:{profile.pane}",
        *window_option(pinned_window(profile, window)),
    ).strip()
    try:
        return int(value)
    except ValueError as err:
        raise RuntimeError(f"surface cursor returned {value!r}") from err


def type_text(
    sid: str, profile: Profile, text: str, window: str | None = None
) -> None:
    # A pinned (deferred) delivery re-proves the destination before every
    # event: verification between chunks is worthless if the pane can be
    # swapped in between two of them.
    verify_pinned_identity(sid, profile, window)
    require_target(sid, profile, window)

    def event(part: str) -> None:
        pinned = (
            ("--pane-id", profile.pane_id) if profile.pane_id else ()
        )
        ctl(
            "session",
            "type",
            "--stdin",
            "--pane",
            profile.pane,
            "--target",
            sid,
            *pinned,
            *window_option(pinned_window(profile, window)),
            input_text=part,
        )

    # Live sends into a redrawing opencode TUI occasionally lose one character
    # from a single large write. Pace the event in small slices; verification
    # and markers stay per whole chunk. Paste is not an alternative here:
    # opencode collapses a bracketed paste into a "[Pasted ~1 lines]" token.
    # Pure-backspace cleanup events skip the pacing: over-backspacing an empty
    # composer is harmless and the rounds repeat anyway.
    if (
        profile.agent == "opencode"
        and len(text) > OPENCODE_TYPE_SLICE
        and text.strip("\x7f")
    ):
        for index in range(0, len(text), OPENCODE_TYPE_SLICE):
            verify_pinned_identity(sid, profile, window)
            event(text[index : index + OPENCODE_TYPE_SLICE])
            time.sleep(OPENCODE_TYPE_PAUSE)
        return
    event(text)


def text_chunks(text: str, max_bytes: int = TYPE_CHUNK_BYTES) -> list[str]:
    """Split normalised text into independently observable input events."""
    max_bytes = max(4, min(TYPE_CHUNK_BYTES, max_bytes))
    chunks: list[str] = []
    current = ""
    tokens = re.findall(r" ?[^ ]+", text)
    for token in tokens:
        if len((current + token).encode("utf-8")) <= max_bytes:
            current += token
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(token.encode("utf-8")) <= max_bytes:
            current = token
            continue

        piece: list[str] = []
        piece_bytes = 0
        for char in token:
            char_bytes = len(char.encode("utf-8"))
            if piece and piece_bytes + char_bytes > max_bytes:
                chunks.append("".join(piece))
                piece = []
                piece_bytes = 0
            piece.append(char)
            piece_bytes += char_bytes
        if piece:
            chunks.append("".join(piece))
    if current:
        chunks.append(current)
    return chunks


def composer_probe_marker(text: str) -> str:
    """Choose a short visible marker that cannot occur in this message."""
    index = 0
    while True:
        marker = f" [peer-check:{index:x}]"
        if marker not in text:
            return marker
        index += 1


def codex_row_is_blank(line: str) -> bool:
    """A row holding nothing but whitespace and idle-animation particles."""
    return not CODEX_PARTICLE_RE.sub(" ", line).strip()


def trailing_input_block(text: str) -> list[str]:
    lines = text.splitlines()[-BOX_LINES:]
    while lines and codex_row_is_blank(lines[-1]):
        lines.pop()
    if lines and CODEX_FOOTER_RE.match(lines[-1]):
        lines.pop()
        while lines and codex_row_is_blank(lines[-1]):
            lines.pop()
    start = len(lines)
    while start and not codex_row_is_blank(lines[start - 1]):
        start -= 1
    return lines[start:]


def codex_live_prompt_text(text: str) -> str | None:
    block = trailing_input_block(text)
    if not block or any(CODEX_SHELL_PROMPT_RE.match(line) for line in block):
        return None
    block = [line for line in block if not codex_row_is_blank(line)]
    if not block:
        return None
    match = CODEX_PROMPT_RE.match(block[0])
    if (
        not match
        or CODEX_CHOICE_RE.match(match.group(1))
        or any(
            not (line[:1].isspace() or CODEX_PARTICLE_RE.match(line[:1]))
            for line in block[1:]
        )
    ):
        return None
    # Wrapped rows carry a two-cell indent, and whatever the animation drew there is
    # decoration. A particle on the first text cell may cover a character, so it stays
    # for row_matches to judge, and trailing particles stay for the same reason. A row
    # whose only character is covered looks like an animation row and is dropped, so
    # such a send fails closed until the particle moves; nothing tells the two apart.
    content = [
        match.group(1),
        *(CODEX_INDENT_RE.sub("", line).strip() for line in block[1:]),
    ]
    return "\n".join(part for part in content if part)


def claude_live_prompt_text(text: str) -> str | None:
    lines = text.splitlines()[-BOX_LINES:]
    for index in range(len(lines) - 1, -1, -1):
        match = CLAUDE_PROMPT_RE.match(lines[index])
        if not match:
            continue
        content = [match.group(1)]
        for offset, line in enumerate(lines[index + 1 :], start=index + 1):
            if RULE_RE.match(line):
                trailing = [item for item in lines[offset + 1 :] if item.strip()]
                if any(
                    not CLAUDE_FOOTER_RE.match(item)
                    or CODEX_CHOICE_RE.match(item.strip())
                    for item in trailing
                ):
                    return None
                return "\n".join(part for part in content if part)
            content.append(line.strip())
        if index == len(lines) - 1:
            return match.group(1)
        return None
    return None


def opencode_inner_text(line: str) -> str | None:
    """Text after the left border, or None when the row is outside the box."""
    if not OPENCODE_BORDER_RE.match(line):
        return None
    return OPENCODE_BORDER_RE.sub("", line, count=1).strip()


OPENCODE_ROW_SPLIT_RE = re.compile(r" {2,}(?=[│┃] {2}\S)")

# A single screen row fits the pane width; anything longer means rows were
# glued by the capture.
UNFLATTEN_MIN_LINE = 165


def opencode_unflatten(text: str) -> list[str]:
    """Restore screen rows from a capture that glued them into one line.

    When the composer box scrolls internally, the pane capture can merge many
    screen rows into a single long line. Rows keep their trailing spaces up to
    the pane width and the next row restarts with its border glyph, so
    splitting before every border run restores them for both border styles.
    """
    lines: list[str] = []
    for line in text.splitlines():
        if len(line) > UNFLATTEN_MIN_LINE:
            pieces: list[str] = []
            last = 0
            for match in OPENCODE_ROW_SPLIT_RE.finditer(line):
                if match.start() == 0:
                    continue
                pieces.append(line[last : match.start()])
                last = match.start()
            pieces.append(line[last:])
            lines.extend(pieces)
        else:
            lines.append(line)
    return lines


def opencode_composer_walk(text: str) -> tuple[str | None, bool]:
    """Walk the composer box upward from the bottom border.

    Returns the draft content above the status row ("" when empty) and whether
    an input row (a blank bordered row) sits between the status row and the
    box top. A non-bordered row ends the box: that is the transcript above it,
    not a parse error.
    """
    lines = opencode_unflatten(text)[-BOX_LINES:]
    bottom = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if OPENCODE_BOTTOM_RE.match(lines[index])
        ),
        None,
    )
    if bottom is None:
        return None, False
    index = bottom - 1

    def inner(row: str) -> str | None:
        return opencode_inner_text(row)

    while index >= 0 and inner(lines[index]) == "":
        index -= 1
    status = inner(lines[index]) if index >= 0 else None
    if status is None or not OPENCODE_STATUS_RE.match(status):
        return None, False
    index -= 1
    block: list[str] = []
    saw_input = False
    while index >= 0:
        row_inner = inner(lines[index])
        if row_inner is None:
            break
        if row_inner == "":
            if block:
                break
            saw_input = True
            index -= 1
            continue
        block.insert(0, row_inner)
        index -= 1
    if not saw_input and block and OPENCODE_PATH_HINT_RE.match(block[-1]):
        # Wide layout renders the cwd hint unconditionally as the bottom-most
        # box row (under any draft). The narrow layout has no hint row at all
        # and always keeps the blank input row below a draft (saw_input).
        block = block[:-1]
    content = [
        row
        for row in block
        if row
        and not OPENCODE_STATUS_RE.match(row)
        and row not in OPENCODE_GLYPH_ROWS
        and not OPENCODE_PLACEHOLDER_RE.match(row)
    ]
    return "\n".join(content), saw_input


def opencode_live_prompt_text(text: str) -> str | None:
    return opencode_composer_walk(text)[0]


def opencode_composer_has_input_area(text: str) -> bool:
    state, saw_input = opencode_composer_walk(text)
    return state is not None and saw_input


def opencode_box_rows(pane: str) -> list[str] | None:
    """Raw bordered rows of the composer box, top-most first; None when the
    capture shows no bottom border (the box cannot be located)."""
    lines = opencode_unflatten(pane)[-BOX_LINES:]
    bottom = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if OPENCODE_BOTTOM_RE.match(lines[index])
        ),
        None,
    )
    if bottom is None:
        return None
    rows: list[str] = []
    for index in range(bottom - 1, -1, -1):
        row = opencode_inner_text(lines[index])
        if row is None:
            break
        rows.append(row)
        if len(rows) >= OPENCODE_DIALOG_SCAN_ROWS:
            break
    rows.reverse()
    return rows


def opencode_dialog_rows_signal(rows: list[str]) -> bool:
    """Signal phrase and choice row together in the given raw rows.

    The phrase alone is not enough — a draft or transcript may quote it — so
    the modal counts only when its choice row (Allow once / Allow always /
    Reject) sits among the same rows.
    """
    texts = []
    for row in rows:
        inner = opencode_inner_text(row)
        texts.append(inner if inner is not None else row.strip())
    has_signal = any(OPENCODE_DIALOG_RE.search(text) for text in texts)
    has_choice = any("Allow" in text and "Reject" in text for text in texts)
    return has_signal and has_choice


def opencode_permission_dialog(pane: str) -> bool:
    """A live permission modal, judged structurally instead of pane-wide.

    With a recognisable composer box, only rows inside the box are judged, so
    a transcript quote of an old dialog above a live input area never blocks
    a send. When no bottom border exists at all, the box is gone — the one
    live state that removes it is a dialog filling the pane — and the bottom
    tail is judged directly. Everything else falls through to the ordinary
    readiness gates, which fail closed.
    """
    rows = opencode_box_rows(pane)
    if rows is not None:
        return opencode_dialog_rows_signal(rows)
    tail = opencode_unflatten(pane)[-OPENCODE_DIALOG_SCAN_ROWS:]
    return opencode_dialog_rows_signal(tail)


def active_modal(profile: Profile, pane: str) -> bool:
    """Whether the captured pane shows a live permission modal."""
    if profile.agent == "opencode":
        return opencode_permission_dialog(pane)
    if profile.agent == "claude":
        return claude_permission_dialog(pane)
    return False


def live_prompt_text(profile: Profile, text: str) -> str | None:
    if profile.agent == "codex":
        return codex_live_prompt_text(text)
    if profile.agent == "opencode":
        return opencode_live_prompt_text(text)
    return claude_live_prompt_text(text)


def codex_placeholder_shown(content: str) -> bool:
    """The empty-composer placeholder, read through the idle animation.

    Rows are joined with the space that wrapping consumed. A particle may stand for
    the placeholder character under it or for an empty cell beside the text, so the
    walk lets every particle take either role and every other cell must match.
    """
    shown = " ".join(row.strip() for row in content.splitlines())
    expected = CODEX_EMPTY_PROMPT
    reached = {0}
    for char in shown:
        advanced = set()
        for index in reached:
            if CODEX_PARTICLE_RE.match(char):
                advanced.add(index)
                if index < len(expected):
                    advanced.add(index + 1)
            elif index < len(expected) and expected[index] == char:
                advanced.add(index + 1)
            elif index == len(expected) and char == " ":
                # Spaces between the particles that trail the placeholder.
                advanced.add(index)
        reached = advanced
        if not reached:
            return False
    return len(expected) in reached


def composer_is_empty(profile: Profile, content: str) -> bool:
    """Recognise known empty-input content for cleanup, acceptance and preflight."""
    if profile.agent == "codex":
        joined = " ".join(content.splitlines())
        return codex_placeholder_shown(content) or bool(
            CODEX_TIP_RE.fullmatch(joined)
        )
    if profile.agent == "opencode":
        return content == ""
    joined = " ".join(content.splitlines())
    return joined in CLAUDE_EMPTY_PROMPTS or bool(
        CLAUDE_STARTUP_HINT_RE.fullmatch(joined)
    )


def composer_is_clear(profile: Profile, state: tuple[str, int] | None) -> bool:
    """Confirmed-empty composer: known-empty content and, for kinds with a
    fixed prompt column, the caret parked there. opencode's prompt column
    varies between render states (indented splash box, wide conversation box),
    so its emptiness is judged on content alone."""
    if state is None or not composer_is_empty(profile, state[0]):
        return False
    return profile.agent == "opencode" or state[1] == EMPTY_CURSOR_COLUMN


def composer_state(
    sid: str, profile: Profile, window: str | None = None
) -> tuple[str, int] | None:
    resolved = require_target(sid, profile, window)
    pane = _pane_text_unchecked(resolved, profile, window)
    if active_modal(profile, pane):
        # A live modal is not a composer state at all: every verification
        # against it must fail closed rather than try to read a draft.
        return None
    content = live_prompt_text(profile, pane)
    if content is None:
        return None
    return content, _cursor_column_unchecked(resolved, profile, window)


def wait_for_composer_change(
    sid: str,
    profile: Profile,
    previous: tuple[str, int],
    settle_delay: float,
    window: str | None = None,
    matches: Callable[[str], bool] | None = None,
) -> tuple[str, int] | None:
    """Wait for a changed composer state to remain stable.

    With ``matches`` the composer counts as settled once the predicate has held
    for ``settle_delay``, whatever the idle animation does to the text meanwhile.
    Without it, a state that differs from ``previous`` has to stay the same for
    that long, a particle on either side standing for the cell it covers.
    """
    deadline = time.monotonic() + PROBE_TIMEOUT
    tolerant = profile.agent == "codex"
    stable_state: tuple[str, int] | None = None
    stable_since: float | None = None
    while True:
        state = composer_state(sid, profile, window)
        now = time.monotonic()
        if matches is not None:
            good = state is not None and matches(state[0])
        else:
            good = state is not None and not same_composer_state(
                state, previous, tolerant
            )
        if good:
            if stable_since is None or (
                matches is None
                and not same_composer_state(state, stable_state, tolerant)
            ):
                stable_state = state
                stable_since = now
            elif now - stable_since >= settle_delay:
                return state
        else:
            stable_state = None
            stable_since = None
        if now >= deadline:
            return None
        time.sleep(PROBE_INTERVAL)


def verify_diag(
    sid: str,
    profile: Profile,
    window: str | None,
    expected_len: int,
    marker: str,
) -> str:
    """One-line observation for a failed chunk verify; no message text."""
    try:
        state = composer_state(sid, profile, window)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        state = None
    if state is None:
        return f"expected ~{expected_len} chars; composer state not recognisable"
    rows = state[0].splitlines() or [""]
    held = "marker present" if marker and marker in state[0] else "marker absent"
    return (
        f"expected ~{expected_len} chars; observed {len(state[0])} chars in "
        f"{len(rows)} rows; {held}"
    )


def type_body(
    sid: str,
    profile: Profile,
    text: str,
    initial: tuple[str, int],
    window: str | None = None,
    progress: BodyProgress | None = None,
) -> tuple[str, int]:
    """Type bounded marked chunks, removing each marker before continuing."""
    if progress is None:
        progress = BodyProgress()
    marker = composer_probe_marker(text)
    tolerant = profile.agent == "codex"
    chunks = text_chunks(text, TYPE_CHUNK_BYTES - len(marker.encode("utf-8")))
    state = initial
    expected = ""
    for index, chunk in enumerate(chunks):
        attempted = expected + chunk
        marked = attempted + marker
        progress.owned_text = marked
        try:
            try:
                type_text(sid, profile, chunk + marker, window)
                changed = wait_for_composer_change(
                    sid,
                    profile,
                    state,
                    CHUNK_SETTLE_DELAY,
                    window,
                    matches=partial(
                        chunk_is_visible,
                        expected=marked,
                        chunk=chunk + marker,
                        tolerant=tolerant,
                    ),
                )
                if changed is None:
                    raise ComposerDirty(
                        f"message chunk {index + 1}/{len(chunks)} was typed but the "
                        "target composer did not confirm it "
                        f"({verify_diag(sid, profile, window, len(marked), marker)}); "
                        "submit withheld",
                        marked,
                    )
                if not composer_has_expected_tail(
                    changed[0], marked, chunk + marker, tolerant
                ):
                    raise ComposerDirty(
                        f"message chunk {index + 1}/{len(chunks)} is incomplete in "
                        "the target composer "
                        f"({verify_diag(sid, profile, window, len(marked), marker)}); "
                        "submit withheld",
                        marked,
                    )
                settle_delay = (
                    COMPOSER_SETTLE_DELAY
                    if index == len(chunks) - 1
                    else CHUNK_SETTLE_DELAY
                )
                # Backspace writes race the redrawing TUI the same way text
                # writes do: some control characters never land, leaving part
                # of the marker behind. Verify the removal and repeat; lost
                # backspaces only under-delete, never over-delete.
                unmarked = None
                for _ in range(3):
                    type_text(sid, profile, "\x7f" * len(marker), window)
                    unmarked = wait_for_composer_change(
                        sid,
                        profile,
                        changed,
                        settle_delay,
                        window,
                        matches=partial(
                            chunk_is_visible,
                            expected=attempted,
                            chunk=chunk,
                            tolerant=tolerant,
                            marker=marker,
                        ),
                    )
                    if unmarked is not None and marker not in unmarked[0]:
                        break
                    unmarked = None
                if unmarked is None or marker in unmarked[0]:
                    raise ComposerDirty(
                        f"message marker after chunk {index + 1}/{len(chunks)} was "
                        "not confirmably removed; submit withheld",
                        marked,
                    )
                if not composer_has_expected_tail(
                    unmarked[0], attempted, chunk, tolerant
                ):
                    raise ComposerDirty(
                        f"message chunk {index + 1}/{len(chunks)} changed during "
                        "marker removal; submit withheld",
                        marked,
                    )
                progress.owned_text = attempted
            except ComposerDirty:
                raise
            except PromptBlocked:
                # A pinned destination that stopped matching (or a dialog that
                # appeared mid-send) must surface as the final refusal it is,
                # not as a retryable body failure.
                raise
            except (
                OSError,
                subprocess.SubprocessError,
                ValueError,
                RuntimeError,
            ) as err:
                raise ComposerDirty(
                    f"message chunk {index + 1}/{len(chunks)} failed after its body "
                    f"write started: {err}; submit withheld",
                    marked,
                ) from err
        except KeyboardInterrupt as err:
            raise ComposerDirty(
                f"interrupted during message chunk {index + 1}/{len(chunks)}; "
                "submit withheld",
                marked,
                interrupted=True,
            ) from err
        expected = attempted
        state = unmarked
    return state


def trailing_particle_variants(row: str) -> list[str]:
    """The row as shown, then with one more trailing particle removed each time.

    A trailing particle may cover the last typed character or an empty cell beside
    the text, and only the source text tells which, so every reading is offered.
    """
    row = row.rstrip()
    variants = [row]
    while row and CODEX_PARTICLE_RE.match(row[-1]):
        # Particles drawn beyond the text sit after the spaces of empty cells.
        row = row[:-1].rstrip()
        variants.append(row)
    return variants


def row_readings(row: str, tolerant: bool) -> list[str]:
    return trailing_particle_variants(row) if tolerant else [row]


def row_matches(expected: str, row: str, tolerant: bool = False) -> bool:
    """Compare one screen row with source text of the same length.

    Codex draws its idle animation over the composer, so with ``tolerant`` a braille
    particle stands for the one cell it covers; every other cell must match exactly,
    and a row of the wrong length never matches. Claude's composer shows no
    animation, so its rows are compared verbatim.
    """
    if not tolerant:
        return expected == row
    if len(expected) != len(row):
        return False
    return all(
        wanted == seen or CODEX_PARTICLE_RE.match(seen) is not None
        for wanted, seen in zip(expected, row)
    )


def same_composer_text(first: str, second: str, tolerant: bool = False) -> bool:
    """Equal composer text; with ``tolerant`` a particle on either side is its cell."""
    if not tolerant:
        return first == second
    rows_first = first.splitlines() or [first]
    rows_second = second.splitlines() or [second]
    if len(rows_first) != len(rows_second):
        return False
    return all(
        any(
            len(one) == len(other)
            and all(
                left == right
                or CODEX_PARTICLE_RE.match(left) is not None
                or CODEX_PARTICLE_RE.match(right) is not None
                for left, right in zip(one, other)
            )
            for one in trailing_particle_variants(row_one)
            for other in trailing_particle_variants(row_other)
        )
        for row_one, row_other in zip(rows_first, rows_second)
    )


def same_composer_state(
    first: tuple[str, int] | None,
    second: tuple[str, int] | None,
    tolerant: bool = False,
) -> bool:
    if first is None or second is None:
        return first is second
    return first[1] == second[1] and same_composer_text(first[0], second[0], tolerant)


def chunk_is_visible(
    content: str,
    expected: str,
    chunk: str,
    tolerant: bool = False,
    marker: str = "",
) -> bool:
    """The typed chunk shows in the composer, without a marker that should be gone."""
    if marker and marker in content:
        return False
    return composer_has_expected_tail(content, expected, chunk, tolerant)


def composer_has_expected_tail(
    content: str,
    expected: str,
    chunk: str,
    tolerant: bool = False,
) -> bool:
    """Match a source suffix while allowing visual line wrapping."""
    positions = {len(expected)}
    rows = content.splitlines() or [content]
    probe_start = max(0, len(expected) - len(chunk) - CHUNK_VERIFY_OVERLAP)
    for index in range(len(rows) - 1, -1, -1):
        readings = row_readings(rows[index], tolerant)
        matched = {
            end - len(row)
            for end in positions
            for row in readings
            if end >= len(row)
            and row_matches(expected[end - len(row) : end], row, tolerant)
        }
        if not matched:
            return False
        if index:
            # Plain screen text omits a source space consumed by visual wrapping.
            positions = matched | {
                start - 1
                for start in matched
                if start and expected[start - 1] == " "
            }
            if 0 in positions:
                # The whole expected suffix is already accounted for by the
                # rows below; anything above it (a busy target's live tool
                # rows, a clipped draft top) is outside the verified suffix.
                return True
            if min(positions) <= probe_start:
                # The chunk region is anchored. A long draft scrolls inside
                # the composer box, so the remaining rows above may simply be
                # out of view; the bottom-up chain already proved the typed
                # chunk contiguously, which is what this check is for.
                return True
        else:
            positions = matched
    probe_start = max(0, len(expected) - len(chunk) - CHUNK_VERIFY_OVERLAP)
    matching_starts = {start for start in positions if start <= probe_start}
    if not matching_starts:
        return False
    if 0 in matching_starts:
        return True
    return not chunk_tail_is_ambiguous(expected, chunk)


def chunk_tail_is_ambiguous(expected: str, chunk: str) -> bool:
    """Reject a clipped tail that could hide a deletion from this chunk."""
    if not chunk:
        return False
    expected_length = len(expected)
    chunk_length = len(chunk)
    visible_length = min(
        expected_length, chunk_length + CHUNK_VERIFY_OVERLAP
    )
    chunk_start = expected_length - chunk_length
    for missing_start in range(chunk_start, expected_length):
        for missing_end in range(missing_start + 1, expected_length + 1):
            missing_length = missing_end - missing_start
            earlier_start = expected_length - missing_length - visible_length
            if earlier_start < 0:
                continue
            if (
                expected[earlier_start:missing_start]
                == expected[expected_length - visible_length : missing_end]
            ):
                return True
    return False


def composer_owned_spans(
    content: str,
    owned_text: str,
    allowed_ends: set[int],
    tolerant: bool = False,
) -> set[tuple[int, int]]:
    """Locate visible contiguous owned text ending at an allowed prefix boundary."""
    if not content:
        return {
            (end - 1, end)
            for end in allowed_ends
            if end == 1 and owned_text[:end] == " "
        }
    valid_ends = {end for end in allowed_ends if 0 < end <= len(owned_text)}
    positions = {(end, end) for end in valid_ends}
    positions.update(
        (end - 1, end) for end in valid_ends if owned_text[end - 1] == " "
    )
    rows = content.splitlines() or [content]
    for index in range(len(rows) - 1, -1, -1):
        readings = row_readings(rows[index], tolerant)
        matched = {
            (position - len(row), end)
            for position, end in positions
            for row in readings
            if position >= len(row)
            and row_matches(owned_text[position - len(row) : position], row, tolerant)
        }
        if not matched:
            return set()
        if index:
            positions = matched | {
                (start - 1, end)
                for start, end in matched
                if start and owned_text[start - 1] == " "
            }
        else:
            positions = matched
    return positions


def wait_for_cleanup_state(
    sid: str,
    profile: Profile,
    previous: tuple[str, int] | None,
    owned_text: str,
    allowed_ends: set[int],
    window: str | None = None,
    accept_empty: bool = True,
) -> tuple[tuple[str, int], set[tuple[int, int]]] | None:
    """Wait for a backspace batch to reach a stable observable state."""
    deadline = time.monotonic() + PROBE_TIMEOUT
    tolerant = profile.agent == "codex"
    stable: tuple[int | None, frozenset[tuple[int, int]]] | None = None
    stable_since: float | None = None
    while True:
        state = composer_state(sid, profile, window)
        now = time.monotonic()
        is_empty = composer_is_clear(profile, state)
        if is_empty and accept_empty:
            return state, set()
        spans = (
            composer_owned_spans(state[0], owned_text, allowed_ends, tolerant)
            if state is not None
            else set()
        )
        if same_composer_state(state, previous, tolerant) or (
            is_empty and not accept_empty
        ):
            stable = None
            stable_since = None
        else:
            candidate = (state[1] if state is not None else None), frozenset(spans)
            if candidate != stable:
                stable = candidate
                stable_since = now
            elif (
                stable_since is not None
                and now - stable_since >= CHUNK_SETTLE_DELAY
            ):
                if state is None:
                    return None
                return state, spans
        if now >= deadline:
            return None
        time.sleep(PROBE_INTERVAL)


def clear_composer(
    sid: str,
    profile: Profile,
    initial: tuple[str, int],
    owned_text: str,
    window: str | None = None,
    reason: list[str] | None = None,
) -> bool:
    """Backspace text owned by this send without interrupting an active turn.

    On failure appends a short cause (no message text) to ``reason`` when given.
    """
    allowed_ends = set(range(1, len(owned_text) + 1))
    settled = wait_for_cleanup_state(
        sid,
        profile,
        initial,
        owned_text,
        allowed_ends,
        window,
        accept_empty=False,
    )
    if settled is None:
        if reason is not None:
            reason.append("composer state never stabilised")
        return False
    state, spans = settled

    def fail(text: str) -> bool:
        if reason is not None:
            reason.append(text)
        return False

    def blind_recovery(state, spans):
        """Bounded blind backspacing; only for a proven-empty pre-write.

        Returns (outcome, state, spans, why) where outcome is True when the
        empty composer was confirmed, False when recovery is hopeless, None
        when fresh spans allow exact deletion to resume; why explains False.
        """
        if profile.agent != "opencode" or initial[0] != "":
            return False, state, spans, ""
        # Blind backspacing assumes every visible character is ours. A draft
        # that is not a tail of the owned text means foreign content shares
        # the composer (a mis-parse or user input); refuse to delete it.
        flat_content = (state[0] or "").replace("\n", "")
        flat_owned = owned_text.replace("\n", "")
        if not flat_owned.endswith(flat_content):
            return False, state, spans, (
                "visible text is not a confirmed tail of the sent text; "
                "blind deletion refused"
            )
        for _ in range(3):
            type_text(sid, profile, "\x7f" * (len(owned_text) + 8), window)
            settled = wait_for_cleanup_state(
                sid,
                profile,
                state,
                owned_text,
                set(range(1, len(owned_text) + 1)),
                window,
            )
            if settled is None:
                return False, state, spans, (
                    "blind backspace batch never reached a stable state"
                )
            state, spans = settled
            if composer_is_clear(profile, state):
                return True, state, spans, ""
            if spans:
                return None, state, spans, ""
        return False, state, spans, "blind recovery did not confirm an empty composer"

    # Each confirmed round deletes at most TYPE_CHUNK_BYTES characters, so a
    # fixed round budget abandoned long drafts mid-way; scale it with length.
    max_rounds = 12
    if owned_text:
        max_rounds = max(12, -(-len(owned_text) // TYPE_CHUNK_BYTES) + 4)
    for _ in range(max_rounds):
        if state == initial or composer_is_clear(profile, state):
            return True
        if not spans:
            outcome, state, spans, why = blind_recovery(state, spans)
            if outcome is True:
                return True
            if outcome is None:
                continue
            return fail(why or "cleanup made no confirmed progress")
        previous_end = max(end for _, end in spans)
        delete_count = min(
            TYPE_CHUNK_BYTES, min(end - start for start, end in spans)
        )
        if not delete_count:
            return fail("no confirmed owned span to delete")
        type_text(sid, profile, "\x7f" * delete_count, window)
        next_ends = {
            candidate
            for _, end in spans
            for candidate in range(end - delete_count, end)
        }
        settled = wait_for_cleanup_state(
            sid, profile, state, owned_text, next_ends, window
        )
        if settled is None:
            return fail("no stable state after a backspace batch")
        state, spans = settled
        if composer_is_clear(profile, state):
            return True
        if not spans or max(end for _, end in spans) >= previous_end:
            # Nothing left to match, or exact deletion stalled on a long
            # corrupted draft; a proven-empty pre-write makes every remaining
            # character ours, corrupted or not.
            outcome, state, spans, why = blind_recovery(state, spans)
            if outcome is True:
                return True
            if outcome is None:
                continue
            return fail(why or "cleanup stalled without confirmed progress")
        allowed_ends = {end for _, end in spans}
    return fail(
        f"deletion rounds exhausted ({max_rounds}); part of the draft may remain"
    )


def wait_for_accepted(
    sid: str,
    profile: Profile,
    composed: tuple[str, int],
    window: str | None = None,
) -> bool:
    """Wait until submission clears the composed state and restores column 2."""
    deadline = time.monotonic() + PROBE_TIMEOUT
    stable_since: float | None = None
    while True:
        state = composer_state(sid, profile, window)
        now = time.monotonic()
        accepted = (
            state is not None
            and composer_is_clear(profile, state)
            and state[0] != composed[0]
        )
        if accepted:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= CHUNK_SETTLE_DELAY:
                return True
        else:
            stable_since = None
        if now >= deadline:
            return False
        time.sleep(PROBE_INTERVAL)


# A peer agent may pre-label its message with its own team-role name
# ("Chat from opencode-lead: "); strip any such prefix before applying ours.
CHAT_LABEL_RE = re.compile(r"^Chat from [^:]{1,40}: ")


def normalize(profile: Profile, message: str) -> str:
    line = " ".join(message.split())
    if any(unicodedata.category(char) == "Cc" for char in line):
        raise ValueError("chat message contains a control character")
    while True:
        match = CHAT_LABEL_RE.match(line)
        if not match:
            break
        line = line[match.end() :].strip()
    if not line:
        raise ValueError("chat message is empty")
    return profile.label + line


def raise_after_composer_dirty(
    sid: str,
    profile: Profile,
    initial: tuple[str, int],
    dirty: ComposerDirty,
    window: str | None = None,
) -> None:
    """Attempt guarded cleanup, then raise with the resulting composer state."""
    reason: list[str] = []
    try:
        cleared = clear_composer(
            sid, profile, initial, dirty.owned_text, window, reason
        )
    except KeyboardInterrupt as cleanup_err:
        raise KeyboardInterrupt(
            f"{dirty}; composer cleanup interrupted"
        ) from cleanup_err
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError):
        cleared = False
    detail = "composer cleared" if cleared else "composer cleanup failed"
    if not cleared and reason:
        detail += f" ({'; '.join(reason)})"
    if dirty.interrupted:
        raise KeyboardInterrupt(f"{dirty}; {detail}") from dirty
    raise ComposerDirty(
        f"{dirty}; {detail}", dirty.owned_text, cleared=cleared
    ) from dirty


def claude_permission_dialog(pane: str) -> bool:
    """A claude permission chooser replaces the input prompt row.

    The chooser options keep their cursor glyph (``❯ 1. Yes``), so a tail with
    numbered options and no standalone input prompt is a modal. Numbered list
    rows inside the transcript coexist with the input prompt and stay harmless.
    """
    tail = [row.strip() for row in pane.splitlines()[-8:]]

    def is_choice(row: str) -> bool:
        return bool(CODEX_CHOICE_RE.match(row.lstrip("❯ ").strip()))

    has_choices = any(is_choice(row) for row in tail)
    has_prompt = any("❯" in row and not is_choice(row) for row in tail)
    return has_choices and not has_prompt


def check_composer_ready(
    sid: str, profile: Profile, window: str | None = None
) -> tuple[str, int]:
    """Pre-write readiness gates; returns the confirmed initial state.

    Raises PromptBlocked for every occupied or unrecognisable target state,
    with a machine-readable `reason` on the exception. Nothing is typed
    before this returns.
    """
    pane = pane_text(sid, profile, window)
    # Distinct, non-retryable refusals for pending dialogs: typing there
    # (or landing the confirm key) would answer for the user.
    if active_modal(profile, pane):
        raise PromptBlocked(
            "target has a pending permission dialog; nothing was typed; "
            "the user must answer it in that pane",
            "permission_dialog",
        )
    empty_text = live_prompt_text(profile, pane)
    if empty_text is None:
        raise PromptBlocked(
            "target composer prompt is not recognisable (shell mode, disabled "
            "input, a trailing modal or status row, or an unknown prompt glyph); "
            "nothing was typed",
            "composer_not_recognisable",
        )
    if (
        profile.agent == "opencode"
        and empty_text == ""
        and not opencode_composer_has_input_area(pane)
    ):
        raise PromptBlocked(
            "target composer is collapsed (agent busy thinking); "
            "nothing was typed",
            "composer_collapsed",
        )
    # Claude's free-form suggestions look like drafts in plain screen text.
    # opencode renders no fixed prompt column (splash box, status rows), so
    # its cursor position is not a usable emptiness signal and is skipped.
    if profile.agent in ("codex", "opencode") and not composer_is_empty(
        profile, empty_text
    ):
        raise PromptBlocked(
            "target composer contains text; nothing was typed",
            "composer_not_empty",
        )
    column = (
        EMPTY_CURSOR_COLUMN
        if profile.agent == "opencode"
        else cursor_column(sid, profile, window)
    )
    if column != EMPTY_CURSOR_COLUMN:
        raise PromptBlocked(
            "target composer is not confirmably empty; nothing was typed",
            "composer_not_confirmed_empty",
        )
    return empty_text, column


def send(
    sid: str,
    profile: Profile,
    message: str,
    window: str | None = None,
    prepared: str | None = None,
    delivery: DeliveryProgress | None = None,
) -> int:
    if prepared is None:
        try:
            typed = normalize(profile, message)
        except KeyboardInterrupt as err:
            raise KeyboardInterrupt(
                "interrupted during message normalisation; nothing was typed"
            ) from err
    else:
        typed = prepared
    try:
        verify_pinned_identity(sid, profile, window)
        initial = check_composer_ready(sid, profile, window)
    except PromptBlocked:
        raise
    except KeyboardInterrupt as err:
        raise KeyboardInterrupt(
            "interrupted during pre-write checks; nothing was typed"
        ) from err
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as err:
        raise RuntimeError(
            f"pre-write check failed; nothing was typed: {err}"
        ) from err

    if delivery is not None:
        delivery.phase = "started"
    phase = "body"
    progress = BodyProgress()
    try:
        try:
            composed = type_body(
                sid, profile, typed, initial, window, progress
            )
            # A particle can hide a wrong character until it moves, so the visible
            # body is verified against the intended text once more with the latest
            # reading. A long body may have scrolled its beginning out of the
            # composer, so the check asks for the visible suffix the final chunk
            # needed, no more.
            before_submit = composer_state(sid, profile, window)
            tolerant = profile.agent == "codex"
            final_chunk = text_chunks(
                typed,
                TYPE_CHUNK_BYTES
                - len(composer_probe_marker(typed).encode("utf-8")),
            )[-1]
            if before_submit is None or not (
                same_composer_state(before_submit, composed, tolerant)
                and composer_has_expected_tail(
                    before_submit[0], typed, final_chunk, tolerant
                )
            ):
                raise ComposerDirty(
                    "target composer changed before submit; submit withheld",
                    typed,
                )
            # Enter the ambiguous phase before Return, so an interrupt at either call
            # boundary cannot be mistaken for a safe pre-submit failure.
            phase = "submit"
            type_text(sid, profile, profile.submit, window)
            phase = "accept"
            accepted = wait_for_accepted(sid, profile, composed, window)
            if not accepted:
                raise DeliveryAmbiguous(
                    "target did not confirm submission; delivery is ambiguous; "
                    "do not resend"
                )
            return len(message)
        except ComposerDirty as err:
            raise_after_composer_dirty(sid, profile, initial, err, window)
        except DeliveryAmbiguous:
            raise
        except PromptBlocked:
            # A destination that stopped matching mid-delivery is a final
            # refusal; cleanup backspaces must not chase the new occupant.
            raise
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as err:
            if phase == "body":
                dirty = ComposerDirty(
                    f"final composer check failed: {err}; submit withheld",
                    progress.owned_text,
                )
                raise_after_composer_dirty(
                    sid, profile, initial, dirty, window
                )
            if phase == "submit":
                detail = "submit request failed"
            else:
                detail = "submission confirmation failed"
            raise DeliveryAmbiguous(
                f"{detail}; delivery is ambiguous; do not resend: {err}"
            ) from err
    except KeyboardInterrupt as err:
        if phase == "body":
            if "; composer " in str(err):
                raise
            dirty = ComposerDirty(
                "interrupted after the message body transaction started; "
                "submit withheld",
                progress.owned_text,
                interrupted=True,
            )
            raise_after_composer_dirty(sid, profile, initial, dirty, window)
        if phase == "submit":
            detail = "submit request was interrupted"
        else:
            detail = "submission confirmation was interrupted"
        raise KeyboardInterrupt(
            f"{detail}; delivery is ambiguous; do not resend"
        ) from err


def send_with_retry(
    sid: str,
    profile: Profile,
    message: str,
    window: str | None = None,
    progress: DeliveryProgress | None = None,
) -> int:
    """Retry a pre-write refusal, or a body failure whose guarded cleanup
    provably restored the empty composer."""
    delivery = progress if progress is not None else DeliveryProgress()
    try:
        prepared = normalize(profile, message)
    except KeyboardInterrupt as err:
        raise KeyboardInterrupt(
            "interrupted during message normalisation; nothing was typed"
        ) from err
    try:
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return send(sid, profile, message, window, prepared, delivery)
            except PromptBlocked as err:
                if err.reason == "permission_dialog":
                    # A dialog needs the user, not another attempt.
                    raise
                if err.reason in IDENTITY_BLOCK_REASONS:
                    # A destination that stopped matching never matches
                    # again by waiting; retrying would only re-probe a
                    # pane this send must not touch.
                    raise
                if attempt == RETRY_ATTEMPTS:
                    raise PromptBlocked(
                        f"{err} after {RETRY_ATTEMPTS} attempts", err.reason
                    ) from err
                print(
                    f"peer-chat: attempt {attempt}/{RETRY_ATTEMPTS} blocked; "
                    f"retrying in {RETRY_DELAY:g}s: {err}",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(RETRY_DELAY)
            except ComposerDirty as err:
                # Submit was withheld, and the structured flag says guarded
                # cleanup provably restored the confirmed empty pre-write
                # state, so the target is provably back where it started and
                # one more full attempt is safe. An unclearable composer is
                # not provable and still refuses.
                if not err.cleared or attempt == RETRY_ATTEMPTS:
                    raise
                # The send never started, so the delivery state returns to
                # its pre-write value for the retry.
                delivery.phase = "not_started"
                print(
                    f"peer-chat: attempt {attempt}/{RETRY_ATTEMPTS} failed to "
                    f"verify but the composer was restored; retrying in "
                    f"{RETRY_DELAY:g}s: {err}",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(RETRY_DELAY)
        raise AssertionError("unreachable")
    except KeyboardInterrupt as wait_err:
        if delivery.phase == "not_started":
            raise KeyboardInterrupt(
                "interrupted while handling a pre-write refusal; "
                "nothing was typed"
            ) from wait_err
        raise


# --- deferred delivery queue -------------------------------------------------
#
# A deferred send is stored in a private SQLite database and delivered by a
# short-lived worker process that is spawned on demand and exits once the
# queue holds no pending records. The queue never answers permission dialogs
# and never waits for a peer's reply: it only waits for the moment when the
# pinned pane is confirmably ready for input, then runs the ordinary guarded
# send. Records are pinned to the pane observed at enqueue time (window,
# session, pane side, pane-id token, socket fingerprint); if any of those
# stops matching, the record is cancelled instead of retargeted.

QUEUE_DIR_ENV = "PEER_CHAT_QUEUE_DIR"
DEFAULT_QUEUE_DIR = Path.home() / ".local" / "state" / "agterm" / "peer-chat"
QUEUE_DB_NAME = "queue.sqlite3"
COORD_LOCK_NAME = "coord.lock"
WORKER_LOCK_NAME = "worker.lock"
WORKER_LOCK_FD_ENV = "PEER_CHAT_WORKER_LOCK_FD"
WORKER_FLAG = "--queue-worker-run"
QUEUE_MAX_PENDING = 128
DEFAULT_TTL_SECONDS = 30 * 60
MAX_TTL_SECONDS = 24 * 3600
METADATA_RETENTION_SECONDS = 24 * 3600.0
PENDING_POLL_SECONDS = 2.0
ACTIVE_STATUSES = frozenset({"pending", "delivering"})
TERMINAL_STATUSES = frozenset({"sent", "cancelled", "expired", "failed", "uncertain"})
PANE_ID_CAPABILITY = "session.type.pane-id"


def queue_dir() -> Path:
    raw = os.environ.get(QUEUE_DIR_ENV)
    return Path(raw).expanduser() if raw else DEFAULT_QUEUE_DIR


def default_socket_path() -> str:
    """The control socket path, mirroring agtermctl's rendezvous order."""
    state_dir = os.environ.get("AGTERM_STATE_DIR")
    if state_dir:
        return str(Path(state_dir) / "agterm.sock")
    return str(
        Path.home() / "Library" / "Application Support" / "agterm" / "agterm.sock"
    )


def guard_private_dir(path: Path, create: bool) -> None:
    """The queue directory must be a private, unlinked-alias, owned dir."""
    if create:
        try:
            path.mkdir(mode=0o700, parents=True)
        except FileExistsError:
            pass
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"queue directory must not be a symlink: {path}")
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError(f"queue directory must be owned by this user: {path}")
    if info.st_mode & 0o077:
        raise ValueError(
            f"queue directory is accessible by other users; run chmod 700 {path}"
        )


def queue_connect(create: bool = False) -> sqlite3.Connection:
    directory = queue_dir()
    guard_private_dir(directory, create)
    db_path = directory / QUEUE_DB_NAME
    if db_path.exists():
        db_info = os.lstat(db_path)
        if stat.S_ISLNK(db_info.st_mode) or not stat.S_ISREG(db_info.st_mode):
            raise ValueError(f"queue database must be a regular file: {db_path}")
        if db_info.st_uid != os.getuid():
            raise ValueError(f"queue database must be owned by this user: {db_path}")
    old_mask = os.umask(0o077)
    try:
        conn = sqlite3.connect(str(db_path), timeout=10.0)
        os.chmod(str(db_path), 0o600)
    finally:
        os.umask(old_mask)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS queue (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT UNIQUE NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            deadline REAL NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            source_bytes INTEGER NOT NULL,
            body TEXT,
            target_name TEXT NOT NULL,
            agent TEXT NOT NULL,
            command TEXT NOT NULL,
            label TEXT NOT NULL,
            submit TEXT NOT NULL,
            window TEXT NOT NULL,
            session TEXT NOT NULL,
            pane TEXT NOT NULL,
            pane_id TEXT NOT NULL,
            socket TEXT NOT NULL,
            sock_dev INTEGER NOT NULL,
            sock_ino INTEGER NOT NULL,
            sock_ctime INTEGER NOT NULL,
            agtermctl TEXT NOT NULL,
            last_block TEXT
        )
        """
    )
    conn.commit()
    return conn


def queue_coord_lock(conn_path: Path):
    """Short advisory lock serialising enqueue/terminal/exit decisions."""

    class _CoordLock:
        def __enter__(self):
            self.fd = os.open(
                str(conn_path),
                os.O_CREAT | os.O_RDWR | os.O_CLOEXEC,
                0o600,
            )
            fcntl.flock(self.fd, fcntl.LOCK_EX)
            return self

        def __exit__(self, *exc):
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            return False

    return _CoordLock()


def coord_lock_path(directory: Path) -> Path:
    return directory / COORD_LOCK_NAME


def purge_terminal_records(conn, now: float) -> None:
    conn.execute(
        f"DELETE FROM queue WHERE status IN ({','.join('?' * len(TERMINAL_STATUSES))}) "
        "AND body IS NULL AND updated_at < ?",
        (*TERMINAL_STATUSES, now - METADATA_RETENTION_SECONDS),
    )
    conn.commit()


def queue_record_public(row) -> dict[str, Any]:
    """Metadata only: never the message body."""
    return {
        "id": row["id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "deadline": row["deadline"],
        "attempts": row["attempts"],
        "source_bytes": row["source_bytes"],
        "target": row["target_name"],
        "agent": row["agent"],
        "pane": row["pane"],
        "session": row["session"],
        "last_block": row["last_block"],
    }


def count_active(conn) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM queue WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))})",
        tuple(ACTIVE_STATUSES),
    ).fetchone()
    return int(row["n"])


def worker_alive(directory: Path, lock_fd: int | None = None) -> bool:
    """Probe the worker lifetime lock without taking it for long."""
    if lock_fd is not None:
        return True
    path = directory / WORKER_LOCK_NAME
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def enqueue_message(
    conn,
    directory: Path,
    body: str,
    profile: Profile,
    target_name: str,
    window: str,
    sid: str,
    pane_id: str,
    socket_path: str,
    fingerprint: tuple[int, int, int],
    agtermctl_path: str,
    ttl_seconds: int,
    delivery_id: str | None,
) -> tuple[dict[str, Any], str]:
    """Insert one deferred record; returns (receipt, worker state)."""
    now = time.time()
    record_id = delivery_id or str(uuid_module.uuid4())
    with queue_coord_lock(coord_lock_path(directory)):
        purge_terminal_records(conn, now)
        existing = conn.execute(
            "SELECT * FROM queue WHERE id = ?", (record_id,)
        ).fetchone()
        if existing is not None:
            same = (
                existing["body"] == body
                and existing["session"] == sid
                and existing["pane"] == profile.pane
            )
            if not same:
                raise ValueError(
                    f"delivery id {record_id} is already used with different "
                    "content or target"
                )
            worker = worker_alive(directory)
            return queue_record_public(existing), ("running" if worker else "started")
        if count_active(conn) >= QUEUE_MAX_PENDING:
            raise RuntimeError(
                f"deferred queue is full ({QUEUE_MAX_PENDING} active records); "
                "cancel or wait before queueing more"
            )
        conn.execute(
            """
            INSERT INTO queue (
                id, created_at, updated_at, deadline, status, attempts,
                source_bytes, body, target_name, agent, command, label,
                submit, window, session, pane, pane_id, socket,
                sock_dev, sock_ino, sock_ctime, agtermctl, last_block
            ) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                record_id,
                now,
                now,
                now + ttl_seconds,
                len(body.encode("utf-8")),
                body,
                target_name,
                profile.agent,
                profile.command,
                profile.label,
                profile.submit,
                window,
                sid,
                profile.pane,
                pane_id,
                socket_path,
                fingerprint[0],
                fingerprint[1],
                fingerprint[2],
                agtermctl_path,
            ),
        )
        row = conn.execute(
            "SELECT * FROM queue WHERE id = ?", (record_id,)
        ).fetchone()
        conn.commit()
        try:
            worker = ensure_queue_worker(directory)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            # The record is safely stored; delivery starts on a later
            # enqueue or status call once the spawn problem is fixed.
            worker = "spawn_failed"
    return queue_record_public(row), worker


def ensure_queue_worker(directory: Path) -> str:
    """Spawn one detached worker unless the lifetime lock says one runs."""
    lock_path = directory / WORKER_LOCK_NAME
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "running"
        script = Path(__file__).resolve()
        python = sys.executable or "python3"
        # Forward the whole PEER_CHAT_* namespace (ours: queue dir, config,
        # test harness hooks — never provider tokens) plus the pinned binary
        # and socket; everything else starts clean, so session selectors and
        # credentials from the sender cannot leak into delivery.
        env = {
            key: value
            for key, value in os.environ.items()
            if key.startswith("PEER_CHAT_")
        }
        env.update(
            {
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "HOME": os.environ.get("HOME", str(Path.home())),
                "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
                "AGTERMCTL": os.environ.get("AGTERMCTL", "agtermctl"),
                QUEUE_DIR_ENV: str(directory),
                WORKER_LOCK_FD_ENV: str(fd),
            }
        )
        env = {key: value for key, value in env.items() if value != ""}
        subprocess.Popen(
            [python, str(script), WORKER_FLAG],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(fd,),
            cwd="/",
        )
        # The child shares the open file description, so the lock survives
        # this close; a second spawn is refused until the worker exits.
        return "started"
    finally:
        os.close(fd)


def target_lock_path(directory: Path, sid: str, pane: str) -> Path:
    digest = hashlib.sha1(f"{sid}:{pane}".encode()).hexdigest()[:16]
    return directory / f"target-{digest}.lock"


def acquire_target_lock(directory: Path, sid: str, pane: str, blocking: bool):
    path = target_lock_path(directory, sid, pane)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        fcntl.flock(fd, flags)
    except OSError:
        os.close(fd)
        return None
    return fd


def release_target_lock(fd: int | None) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(fd)


def guard_sync_target(conn, sid: str, pane: str) -> None:
    """A synchronous send must not start while deliveries are queued."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM queue WHERE status = 'pending' "
        "AND session = ? AND pane = ?",
        (sid, pane),
    ).fetchone()
    if int(row["n"]):
        raise RuntimeError(
            f"{row['n']} deferred message(s) are queued for this pane; "
            "deliver them first (--queue-status) or send with --defer"
        )


def resolve_defer_target(
    args: argparse.Namespace, profile: Profile
) -> tuple[str, str, str, tuple[int, int, int], str]:
    """Pin the destination observed right now: pane, token, socket, binary."""
    window, sid = resolve_target(args.session, args.window, profile)
    info = find_node(sid, window)
    if profile.pane is None:
        profile.pane = resolve_pane(info, profile)
    token = (
        info.get("paneID") if profile.pane == "left" else info.get("splitPaneID")
    )
    if not token:
        raise RuntimeError(
            "target pane has no stable pane-id token; --defer requires agterm "
            f"with the {PANE_ID_CAPABILITY} capability"
        )
    socket_path = os.environ.get("PEER_CHAT_SOCKET") or default_socket_path()
    fingerprint = socket_fingerprint(socket_path)
    binary = os.environ.get("AGTERMCTL", "agtermctl")
    resolved = shutil.which(binary)
    if not resolved:
        raise RuntimeError(f"agtermctl binary not found on PATH: {binary!r}")
    return window, sid, token, fingerprint, resolved


def check_defer_capability() -> None:
    payload = tree()
    app = (payload.get("result", {}).get("tree") or {}).get("app") or {}
    capabilities = app.get("capabilities") or []
    if PANE_ID_CAPABILITY not in capabilities:
        raise RuntimeError(
            f"--defer requires agterm with the {PANE_ID_CAPABILITY} capability"
        )


def classify_delivery_error(err: Exception) -> tuple[str, str]:
    """Map a failed delivery to (status, last_block) without message text."""
    if isinstance(err, PromptBlocked):
        if err.reason in IDENTITY_BLOCK_REASONS:
            return "failed", err.reason
        return "pending", err.reason
    if isinstance(err, DeliveryAmbiguous):
        return "uncertain", "ambiguous_submit"
    if isinstance(err, ComposerDirty):
        return "failed", "cleanup_failed"
    if isinstance(err, KeyboardInterrupt):
        return "uncertain", "interrupted"
    return "failed", "delivery_error"


def deliver_pending_row(row) -> tuple[str, str]:
    """One delivery attempt for a claimed row; returns (status, last_block)."""
    profile = Profile(
        agent=row["agent"],
        command=row["command"],
        label=row["label"],
        submit=row["submit"],
        pane=row["pane"],
        window=row["window"],
        session=row["session"],
        pane_id=row["pane_id"],
        socket=row["socket"],
        socket_fingerprint=(row["sock_dev"], row["sock_ino"], row["sock_ctime"]),
        agtermctl=row["agtermctl"],
    )
    os.environ["AGTERMCTL"] = row["agtermctl"]
    os.environ["PEER_CHAT_SOCKET"] = row["socket"]
    lock_fd = acquire_target_lock(queue_dir(), row["session"], row["pane"], True)
    try:
        send_with_retry(
            row["session"], profile, row["body"], window=row["window"]
        )
    except Exception as err:  # classified below; never leaks message text
        status, block = classify_delivery_error(err)
        debug_log = os.environ.get("PEER_CHAT_DEBUG_DELIVERY")
        if debug_log:
            # Operator-enabled troubleshooting only; never on by default.
            import traceback

            with open(debug_log, "a", encoding="utf-8") as fh:
                fh.write(f"--- delivery of {row['id']} -> {status}/{block}\n")
                traceback.print_exc(file=fh)
        return status, block
    finally:
        release_target_lock(lock_fd)
    return "sent", ""


def run_queue_worker() -> int:
    """Deliver pending records until the queue holds none; then exit.

    Started only by ensure_queue_worker, which holds the lifetime lock and
    passes its file descriptor over. A `delivering` row found at startup
    belonged to a dead worker whose send may have progressed past the submit
    key: it becomes `uncertain` and is never retried.
    """
    directory = queue_dir()
    guard_private_dir(directory, create=False)
    lock_fd = int(os.environ[WORKER_LOCK_FD_ENV])
    conn = queue_connect(create=True)
    coord = queue_coord_lock(coord_lock_path(directory))
    with coord:
        conn.execute(
            "UPDATE queue SET status = 'uncertain', body = NULL, "
            "updated_at = ?, last_block = 'worker_died' WHERE status = 'delivering'",
            (time.time(),),
        )
        conn.commit()
    while True:
        rows = conn.execute(
            "SELECT * FROM queue WHERE status = 'pending' ORDER BY seq"
        ).fetchall()
        if rows:
            seen_targets: set[tuple[str, str]] = set()
            for row in rows:
                key = (row["session"], row["pane"])
                if key in seen_targets:
                    continue
                seen_targets.add(key)
                now = time.time()
                if row["deadline"] < now:
                    with coord:
                        conn.execute(
                            "UPDATE queue SET status = 'expired', body = NULL, "
                            "updated_at = ? WHERE id = ? AND status = 'pending'",
                            (now, row["id"]),
                        )
                        conn.commit()
                    continue
                with coord:
                    claimed = conn.execute(
                        "UPDATE queue SET status = 'delivering', "
                        "updated_at = ?, attempts = attempts + 1 "
                        "WHERE id = ? AND status = 'pending' RETURNING id",
                        (now, row["id"]),
                    ).fetchall()
                    # The claim must be durable BEFORE any typing starts:
                    # a worker killed mid-delivery has to leave a `delivering`
                    # record behind, which the next worker turns into
                    # `uncertain` instead of typing the message a second time.
                    conn.commit()
                if not claimed:
                    continue
                status, last_block = deliver_pending_row(row)
                with coord:
                    purge_terminal_records(conn, time.time())
                    if status in TERMINAL_STATUSES:
                        conn.execute(
                            "UPDATE queue SET status = ?, body = NULL, "
                            "updated_at = ?, last_block = ? WHERE id = ?",
                            (status, time.time(), last_block or None, row["id"]),
                        )
                    else:
                        # A blocked head returns to pending with its body
                        # intact: the next pass retries the delivery.
                        conn.execute(
                            "UPDATE queue SET status = 'pending', "
                            "updated_at = ?, last_block = ? WHERE id = ?",
                            (time.time(), last_block or None, row["id"]),
                        )
                    conn.commit()
        # Re-check under the coordination lock so an enqueue racing this
        # decision is never lost: it either sees the lifetime lock held and
        # skips spawning, or it inserted before this final count. The
        # lifetime lock is released inside the same critical section, so a
        # waiting spawner can never observe a dying worker as "running".
        exit_now = False
        with coord:
            conn.execute(
                "UPDATE queue SET status = 'expired', body = NULL, "
                "updated_at = ? WHERE status = 'pending' AND deadline < ?",
                (time.time(), time.time()),
            )
            conn.commit()
            remaining = conn.execute(
                f"SELECT COUNT(*) AS n FROM queue WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))})",
                tuple(ACTIVE_STATUSES),
            ).fetchone()
            if not int(remaining["n"]):
                os.close(lock_fd)
                exit_now = True
        if exit_now:
            break
        time.sleep(PENDING_POLL_SECONDS)
    return 0


def queue_status(conn, record_id: str | None) -> Any:
    directory = queue_dir()
    now = time.time()
    with queue_coord_lock(coord_lock_path(directory)):
        purge_terminal_records(conn, now)
        conn.commit()
        if record_id:
            row = conn.execute(
                "SELECT * FROM queue WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError(f"no queued record with id {record_id!r}")
            result: Any = queue_record_public(row)
        else:
            rows = conn.execute(
                "SELECT * FROM queue ORDER BY status = 'pending' DESC, "
                "status = 'delivering' DESC, seq"
            ).fetchall()
            result = [queue_record_public(row) for row in rows]
    return {
        "records": result,
        "worker": "running" if worker_alive(directory) else "stopped",
    }


def queue_cancel(conn, record_id: str) -> dict[str, Any]:
    directory = queue_dir()
    with queue_coord_lock(coord_lock_path(directory)):
        row = conn.execute(
            "SELECT * FROM queue WHERE id = ?", (record_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError(f"no queued record with id {record_id!r}")
        if row["status"] != "pending":
            raise RuntimeError(
                f"record {record_id} is {row['status']!r}; only pending "
                "records can be cancelled"
            )
        conn.execute(
            "UPDATE queue SET status = 'cancelled', body = NULL, "
            "updated_at = ? WHERE id = ?",
            (time.time(), record_id),
        )
        conn.commit()
    return {"cancelled": record_id}


def defer_message(args: argparse.Namespace, profile: Profile) -> int:
    """Store one message for later delivery and print the queue receipt."""
    check_defer_capability()
    window, sid, pane_id, fingerprint, agtermctl_path = resolve_defer_target(
        args, profile
    )
    message = read_message(args.stdin, args.message_file)
    try:
        body = normalize(profile, message)
    except KeyboardInterrupt as err:
        raise KeyboardInterrupt(
            "interrupted during message normalisation; nothing was queued"
        ) from err
    conn = queue_connect(create=True)
    try:
        record, worker = enqueue_message(
            conn,
            queue_dir(),
            body,
            profile,
            args.to,
            window,
            sid,
            pane_id,
            os.environ.get("PEER_CHAT_SOCKET") or default_socket_path(),
            fingerprint,
            agtermctl_path,
            args.ttl,
            args.delivery_id,
        )
    finally:
        conn.close()
    if worker == "spawn_failed":
        print(
            "peer-chat: queue worker could not be started; the record is "
            f"stored ({record['id']}) and will be delivered by a later "
            "enqueue once the spawn problem is fixed",
            file=sys.stderr,
            flush=True,
        )
    print(
        json.dumps(
            {
                "queued": len(body),
                "id": record["id"],
                "deadline": record["deadline"],
                "worker": worker,
            }
        ),
        flush=True,
    )
    return 0


def ttl_argument(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("TTL must be an integer") from err
    if seconds <= 0 or seconds > MAX_TTL_SECONDS:
        raise argparse.ArgumentTypeError(
            f"TTL must be between 1 and {MAX_TTL_SECONDS} seconds"
        )
    return seconds


def delivery_id_argument(value: str) -> str:
    try:
        uuid_module.UUID(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("delivery id must be a UUID") from err
    return value


def message_name(value: str) -> str:
    if not MESSAGE_NAME_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "message name must match peer-chat-<sender>-<suffix>.txt"
        )
    return value


def selector_argument(value: str) -> str:
    selector = value.strip()
    if not selector:
        raise argparse.ArgumentTypeError("selector must not be empty")
    return selector


def private_spool_fd(create: bool) -> int:
    if create:
        try:
            MESSAGE_SPOOL.mkdir(mode=0o700)
        except FileExistsError:
            pass
    spool_fd = os.open(
        MESSAGE_SPOOL,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    info = os.fstat(spool_fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        os.close(spool_fd)
        raise ValueError("message spool must be a private directory owned by this user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        os.close(spool_fd)
        raise ValueError(
            f"message spool is accessible by other users; run chmod 700 {MESSAGE_SPOOL}"
        )
    return spool_fd


def prepare_message(name: str) -> Path:
    spool_fd = private_spool_fd(create=True)
    try:
        message_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=spool_fd,
        )
        os.fchmod(message_fd, 0o600)
        os.close(message_fd)
    finally:
        os.close(spool_fd)
    return MESSAGE_SPOOL / name


def read_message(use_stdin: bool, message_file: str | None) -> str:
    if use_stdin:
        binary = getattr(sys.stdin, "buffer", None)
        if binary is not None:
            body = binary.read(MAX_MESSAGE_BYTES + 1)
            if len(body) > MAX_MESSAGE_BYTES:
                raise ValueError(f"stdin message exceeds {MAX_MESSAGE_BYTES} bytes")
            return body.decode("utf-8")

        message = sys.stdin.read(MAX_MESSAGE_BYTES + 1)
        if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError(f"stdin message exceeds {MAX_MESSAGE_BYTES} bytes")
        return message
    if message_file is None:
        raise ValueError("provide --stdin or --message-file NAME")

    spool_fd = private_spool_fd(create=False)
    message_fd = -1
    try:
        message_fd = os.open(
            message_file,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=spool_fd,
        )
        info = os.fstat(message_fd)
        entry = os.stat(message_file, dir_fd=spool_fd, follow_symlinks=False)
        same_entry = (entry.st_dev, entry.st_ino) == (info.st_dev, info.st_ino)
        is_owned_regular = (
            same_entry
            and stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and info.st_nlink == 1
        )
        if is_owned_regular:
            os.unlink(message_file, dir_fd=spool_fd)
        if not is_owned_regular:
            raise ValueError("message file must be an owned regular file with one link")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError(
                "message file is accessible by other users and was consumed; "
                "run --prepare-message again"
            )
        if info.st_size > MAX_MESSAGE_BYTES:
            raise ValueError(
                f"message file exceeds {MAX_MESSAGE_BYTES} bytes and was consumed; "
                "run --prepare-message again"
            )

        with os.fdopen(message_fd, encoding="utf-8") as stream:
            message_fd = -1
            message = stream.read(MAX_MESSAGE_BYTES + 1)
        if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError(
                f"message file exceeds {MAX_MESSAGE_BYTES} bytes and was consumed; "
                "run --prepare-message again"
            )
        return message
    finally:
        if message_fd >= 0:
            os.close(message_fd)
        os.close(spool_fd)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--to", help="target agent name from the peer-chat config"
    )
    parser.add_argument("--session", type=selector_argument)
    parser.add_argument(
        "--window",
        type=selector_argument,
        help="agterm window id or prefix (defaults to AGTERM_WINDOW_ID)",
    )
    parser.add_argument(
        "--target-command",
        type=command_name,
        metavar="NAME",
        help="target agent executable or wrapper name",
    )
    parser.add_argument(
        "--pane",
        choices=("left", "right"),
        help="pick the target split pane when both panes run the same command",
    )
    parser.add_argument(
        "--queue",
        action="store_true",
        help="queue a Codex message with Tab instead of steering with Return",
    )
    parser.add_argument(
        "--defer",
        action="store_true",
        help="store the message in the delivery queue; a short-lived worker "
        "delivers it when the pinned target pane is ready for input",
    )
    parser.add_argument(
        "--ttl",
        type=ttl_argument,
        metavar="SECONDS",
        help=f"give up deferred delivery after this long (default {DEFAULT_TTL_SECONDS})",
    )
    parser.add_argument(
        "--delivery-id",
        type=delivery_id_argument,
        metavar="UUID",
        help="idempotency key for --defer: repeating the same id with the "
        "same message returns the original record",
    )
    parser.add_argument(
        "--queue-status",
        nargs="?",
        const="",
        default=None,
        metavar="ID",
        help="list queued records, or show one by id",
    )
    parser.add_argument(
        "--queue-cancel",
        type=selector_argument,
        metavar="ID",
        help="cancel one pending queued record by id",
    )
    parser.add_argument(
        WORKER_FLAG,
        action="store_true",
        help=argparse.SUPPRESS,
    )
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--stdin", action="store_true")
    source.add_argument(
        "--message-file",
        type=message_name,
        metavar="NAME",
        help="consume a prepared UTF-8 message from the private spool",
    )
    source.add_argument(
        "--prepare-message",
        type=message_name,
        metavar="NAME",
        help="create a private one-shot message file and print its path",
    )
    args = parser.parse_args(argv)
    queue_read_modes = [
        bool(args.prepare_message),
        args.queue_status is not None,
        bool(args.queue_cancel),
        args.queue_worker_run,
    ]
    if sum(queue_read_modes) > 1:
        parser.error(
            "--prepare-message, --queue-status, --queue-cancel and the "
            "worker mode are mutually exclusive"
        )
    if args.prepare_message:
        if (
            args.to
            or args.session
            or args.window
            or args.target_command
            or args.pane
            or args.queue
            or args.defer
            or args.ttl is not None
            or args.delivery_id
        ):
            parser.error("--prepare-message does not accept target options")
        return args
    if args.queue_worker_run:
        return args
    if args.queue_status is not None or args.queue_cancel:
        if (
            args.to
            or args.session
            or args.window
            or args.target_command
            or args.pane
            or args.queue
            or args.defer
            or args.ttl is not None
            or args.delivery_id
            or args.stdin
            or args.message_file
        ):
            parser.error(
                "--queue-status/--queue-cancel do not accept sending options"
            )
        return args
    if args.ttl is not None and not args.defer:
        parser.error("--ttl is only valid with --defer")
    if args.delivery_id and not args.defer:
        parser.error("--delivery-id is only valid with --defer")
    if args.defer and args.ttl is None:
        args.ttl = DEFAULT_TTL_SECONDS
    if not args.to:
        parser.error("--to is required when sending")
    if not args.stdin and not args.message_file:
        parser.error("provide --stdin or --message-file")
    if args.queue:
        spec = load_agents().get(args.to)
        if spec is not None and spec["kind"] != "codex":
            parser.error("--queue is available only for a codex target")
    return args


def report_success(sent: int) -> None:
    """Emit the machine-readable receipt after delivery was confirmed."""
    print(json.dumps({"sent": sent}), flush=True)


def run_main(progress: DeliveryProgress) -> int:
    """Run one CLI operation inside main's outer signal boundary."""
    args = parse_args()
    if args.prepare_message:
        path = prepare_message(args.prepare_message)
        print(json.dumps({"messageFile": str(path)}))
        return 0
    if args.queue_worker_run:
        return run_queue_worker()
    if args.queue_status is not None:
        conn = queue_connect(create=True)
        try:
            print(json.dumps(queue_status(conn, args.queue_status or None)))
        finally:
            conn.close()
        return 0
    if args.queue_cancel:
        conn = queue_connect(create=True)
        try:
            print(json.dumps(queue_cancel(conn, args.queue_cancel)))
        finally:
            conn.close()
        return 0
    profile = target_profile(
        args.to, args.target_command, args.queue, args.pane
    )
    if args.defer:
        return defer_message(args, profile)
    window, sid = resolve_target(args.session, args.window, profile)
    directory = queue_dir()
    # The target lock needs a private home; the first synchronous send in a
    # checkout creates it.
    guard_private_dir(directory, create=True)
    conn = queue_connect(create=True)
    try:
        guard_sync_target(conn, sid, profile.pane)
    finally:
        conn.close()
    lock_fd = acquire_target_lock(directory, sid, profile.pane, blocking=False)
    if lock_fd is None:
        raise RuntimeError(
            "another delivery is in progress for this pane; nothing was typed"
        )
    try:
        message = read_message(args.stdin, args.message_file)
        sent = send_with_retry(sid, profile, message, window, progress)
    finally:
        release_target_lock(lock_fd)
    progress.phase = "confirmed"
    report_success(sent)
    return 0


def main() -> int:
    progress = DeliveryProgress()
    try:
        return run_main(progress)
    except KeyboardInterrupt as err:
        detail_text = str(err)
        if not detail_text and progress.phase == "confirmed":
            detail_text = (
                "delivery was confirmed; do not resend; success report interrupted"
            )
        elif not detail_text and progress.phase == "started":
            detail_text = "delivery status is unavailable; do not resend"
        elif not detail_text and progress.phase == "not_started":
            detail_text = "nothing was typed"
        detail = f": {detail_text}" if detail_text else ""
        print(f"peer-chat: interrupted{detail}", file=sys.stderr)
        return 130
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as err:
        if progress.phase == "confirmed":
            print(
                "peer-chat: delivery was confirmed; do not resend; "
                f"success report failed: {err}",
                file=sys.stderr,
            )
            return 1
        print(f"peer-chat: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
