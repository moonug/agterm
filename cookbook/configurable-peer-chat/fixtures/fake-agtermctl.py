#!/usr/bin/env python3
"""A deterministic offline stand-in for agtermctl, driven by a state file.

The state file path comes from PEER_CHAT_FAKE_STATE. Every invocation re-reads
the state, performs the one command, and writes the state back, so tests
mutate conditions between and during deliveries. Typing goes through the same
gates the real server applies for deferred sends: an explicit --socket and a
--pane-id that must match the pane's live token.
"""

import json
import os
import sys
import time

DIALOG_SCREEN = """     ▣  Build · GPT-6 Astra
  ┃
  ┃  △ Permission required
  ┃    ← Access external directory ~/example/apps
  ┃
  ┃  Patterns
  ┃
  ┃  - /Users/example/apps/*
  ┃
  ┃
  ┃   Allow once   Allow always   Reject                       enter confirm
  ┃
"""

BOTTOM_BORDER = "  ╹" + "▀" * 70


def load_state():
    with open(os.environ["PEER_CHAT_FAKE_STATE"], encoding="utf-8") as fh:
        return json.load(fh)


def save_state(state):
    tmp = os.environ["PEER_CHAT_FAKE_STATE"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, os.environ["PEER_CHAT_FAKE_STATE"])


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def parse_options(argv):
    options = {}
    rest = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--socket":
            options["socket"] = argv[index + 1]
            index += 2
        elif arg == "--window":
            options["window"] = argv[index + 1]
            index += 2
        elif arg == "--pane":
            options["pane"] = argv[index + 1]
            index += 2
        elif arg == "--pane-id":
            options["pane_id"] = argv[index + 1]
            index += 2
        elif arg == "--target":
            options["target"] = argv[index + 1]
            index += 2
        elif arg == "--lines":
            options["lines"] = argv[index + 1]
            index += 2
        elif arg in ("--json", "--stdin", "--all"):
            options[arg] = True
            index += 1
        else:
            rest.append(arg)
            index += 1
    return options, rest


def require_socket(options, state):
    given = options.get("socket")
    if given is not None and given != state["socket"]:
        fail(f"wrong socket: {given!r}")


def session_by_id(state, sid):
    for info in state["sessions"]:
        if info["id"] == sid:
            return info
    fail(f"no such session: {sid}")


def render_screen(state):
    composer = state.get("composer", "empty")
    if composer == "dialog":
        return DIALOG_SCREEN
    if composer == "collapsed":
        return "  ┃  Build · Test · high\n" + BOTTOM_BORDER
    if composer == "shell":
        return "zsh-5.9$\n"
    buffer = state.get("buffer", "")
    rows = []
    remaining = buffer
    while remaining:
        rows.append("  ┃  " + remaining[:100])
        remaining = remaining[100:]
    if not rows:
        rows = ["  ┃"]
    rows += ["  ┃", "  ┃  Build · Test · high", BOTTOM_BORDER]
    return "\n".join(rows) + "\n"


def apply_type_event(state, options, text):
    sid = options.get("target")
    info = session_by_id(state, sid)
    pane = options.get("pane", "left")
    if pane not in ("left", "right"):
        fail(f"unsupported pane {pane!r}")
    token = options.get("pane_id")
    live = info["paneID"] if pane == "left" else info.get("splitPaneID")
    # the real server enforces the token only when the caller passes one;
    # unpinned synchronous sends legitimately omit it
    if token is not None and token != live:
        fail("pane identity mismatch")
    debug_log = os.environ.get("PEER_CHAT_FAKE_TYPELOG")
    if debug_log:
        with open(debug_log, "a", encoding="utf-8") as fh:
            fh.write(
                f"pid={os.getpid()} ppid={os.getppid()} pane={pane} "
                f"len={len(text)} text={text!r}\n"
            )
    delay = float(state.get("type_delay", 0))
    if delay:
        time.sleep(delay)
    if text == "\n":
        state["submitted"] = int(state.get("submitted", 0)) + 1
        if state.get("submit_ok", True):
            state["buffer"] = ""
    elif text and set(text) == {"\x7f"}:
        state["buffer"] = state.get("buffer", "")[: -len(text)]
    else:
        state["buffer"] = state.get("buffer", "") + text
    events = state.setdefault("events", [])
    events.append({"pane": pane, "target": sid, "length": len(text)})


def main():
    argv = sys.argv[1:]
    options, rest = parse_options(argv)
    state = load_state()
    require_socket(options, state)
    command = " ".join(rest)
    if rest[:1] == ["tree"]:
        payload = {
            "ok": True,
            "result": {
                "tree": {
                    "app": {
                        "version": "0.28.0",
                        "capabilities": state.get(
                            "capabilities", ["session.type.pane-id"]
                        ),
                    },
                    "workspaces": [
                        {
                            "id": "ws",
                            "name": "w",
                            "active": True,
                            "sessions": state["sessions"],
                        }
                    ],
                }
            },
        }
        print(json.dumps(payload))
    elif rest[:2] == ["window", "list"]:
        print(
            json.dumps({"ok": True, "result": {"windows": state["windows"]}})
        )
    elif rest[:2] == ["session", "text"]:
        print(render_screen(state), end="")
    elif rest[:2] == ["surface", "cursor"]:
        print("2")
    elif rest[:2] == ["session", "type"]:
        text = sys.stdin.read()
        apply_type_event(state, options, text)
        save_state(state)
        return
    else:
        fail(f"fake agtermctl does not implement: {command!r}")
    save_state(state)


if __name__ == "__main__":
    main()
