#!/usr/bin/env python3
"""Create one explicitly pinned fresh pair; task packets are consumed at most once."""

import argparse
import fcntl
import json
import os
import re
import select
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SIDES = ("left", "right")
SCRIPT = str(Path(__file__).resolve())


def require(ok, message):
    if not ok:
        raise ValueError(message)


def text(value):
    require(isinstance(value, str) and value.strip() and "\0" not in value,
            "expected nonblank text without NUL")
    return value


def identity(value):
    value = text(value)
    require(str(uuid.UUID(value)).lower() == value.lower(), "full UUID required")
    return value


def executable(value):
    result = shutil.which(text(value))
    require(result, f"executable unavailable: {value}; supply a trusted executable wrapper, not a function")
    return str(Path(result).resolve())


def private(path, directory=False):
    info = path.lstat()
    require(not path.is_symlink() and info.st_uid == os.getuid(), "unsafe request ownership")
    require(stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600),
            "request directories must be 700 and files 600")


def put(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                   "w", encoding="utf-8", newline="") as stream:
        stream.write(value)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def addressed(meta, args):
    options = ["--socket", meta["socket"]]
    if args[0] != "version" and tuple(args[:2]) != ("window", "list"):
        options.extend(["--window", meta["window"]])
    return [meta["ctl"], *args, *options]


def ctl(meta, *args):
    result = subprocess.run([*addressed(meta, args), "--json"],
                            capture_output=True, text=True, timeout=meta["timeout"], check=False)
    # stdout may contain source-agent prompts in foreground argv; never log it.
    require(result.returncode == 0,
            f"control command failed: {' '.join(args[:2])} (exit {result.returncode}): "
            + result.stderr.strip()[:2000])
    data = json.loads(result.stdout)
    require(data.get("ok") is True, "control response not successful")
    result = data["result"]
    return result["tree"] if args == ("tree",) else result


def session(tree, sid):
    found = [s for w in tree.get("workspaces", []) for s in w.get("sessions", [])
             if s.get("id", "").lower() == sid.lower()]
    require(len(found) == 1, f"exact session not found: {sid}")
    return found[0]


def runs(argv, command):
    return (isinstance(argv, list) and bool(argv) and
            all(isinstance(a, str) for a in argv) and
            any(Path(a).name == command for a in argv[:2]))


def request_path(root, run_id):
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id), "invalid request ID")
    root = Path(root).absolute()
    private(root, True)
    return root / run_id


def load(args):
    path = request_path(args.state_dir, args.request)
    private(path, True)
    private(path / "meta.json")
    return path, read_json(path / "meta.json")


@contextmanager
def locked(path):
    with (path / "lock").open("r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def abort(path):
    with locked(path):
        abort_locked(path)


def abort_locked(path):
    if not (path / "aborted").exists():
        put(path / "aborted", "delivery ambiguous; never repeat")
    for side in SIDES:
        (path / (side + ".txt")).unlink(missing_ok=True)


def active(path):
    require(not (path / "aborted").exists(), "request aborted; delivery ambiguous; never repeat")


def pane_ids(node):
    require(node.get("hasSplit") is True, "original split disappeared")
    ids = {"left": identity(node.get("paneID")).lower(),
           "right": identity(node.get("splitPaneID")).lower()}
    require(ids["left"] != ids["right"], "pane identities must differ")
    return ids


def live_pair(path, meta):
    tree = ctl(meta, "tree")
    expected = read_json(path / "pair.json")
    require(pane_ids(session(tree, (path / "created").read_text())) == expected,
            "original pair swapped, promoted or replaced")
    return expected


def process_identity(pid):
    if sys.platform == "linux":
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[1]), fields[19]
    result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "ppid=", "-o", "lstart="],
                            capture_output=True, text=True, check=True, timeout=2,
                            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"})
    parent, birth = result.stdout.strip().split(maxsplit=1)
    return int(parent), birth


def original_descendant(record):
    pid = os.getppid()
    seen = set()
    while pid > 1 and pid not in seen and len(seen) < 256:
        seen.add(pid)
        parent, birth = process_identity(pid)
        if pid == record["pid"]:
            require(birth == record["birth"], "original agent process birth changed")
            return
        pid = parent
    raise ValueError("caller is not a descendant of the original live agent process")


def owner(path, meta, side):
    active(path)
    sid, window, socket = pinned_pane(path, meta, side)
    private(path / (side + ".launched"))
    record = read_json(path / (side + ".launched"))
    expected = live_pair(path, meta)
    require(record["panes"] == expected and record["session"] == sid.lower() and
            expected[side] == os.environ["AGTERM_PANE_ID"].lower(), "launch identity mismatch")
    original_descendant(record)
    return sid, window, socket, expected


def pinned_pane(path, meta, side):
    require(side in SIDES, "unsupported pane role")
    sid = identity(os.environ.get("AGTERM_SESSION_ID"))
    window = identity(os.environ.get("AGTERM_WINDOW_ID"))
    socket = text(os.environ.get("AGTERM_SOCKET"))
    require(sid.lower() != meta["source"].lower(), "refusing source pane")
    require(window.lower() == meta["window"].lower() and socket == meta["socket"],
            "new pane window/socket mismatch")
    require(os.environ.get("AGTERM_PANE") == side, "pane role changed or unavailable")
    deadline = time.monotonic() + meta["timeout"]
    while not (path / "pair.json").exists() and time.monotonic() < deadline:
        active(path)
        time.sleep(0.02)
    active(path)
    private(path / "created")
    require((path / "created").read_text().lower() == sid.lower(), "created session mismatch")
    identity(os.environ.get("AGTERM_PANE_ID"))
    return sid, window, socket


def bootstrap(args):
    env = ["env", *(f"{key}={os.environ[key]}" for key in
                    ("AGTERM_SESSION_ID", "AGTERM_WINDOW_ID", "AGTERM_SOCKET", "AGTERM_PANE",
                     "AGTERM_PANE_ID"))]
    command = shlex.join([*env, sys.executable, SCRIPT, "consume", "--state-dir", args.state_dir,
                          "--request", args.request, "--side", args.side])
    return ("Run this local command exactly once and follow its output:\n```sh\n" + command +
            "\n```\nIf it fails or reports already consumed, STOP; do not reconstruct or repeat any task. "
            "Do not answer trust/auth/permission prompts automatically.")


def watch_parent(path, side, parent, ready):
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        os.setsid()
        with open(os.devnull, "r+b", buffering=0) as stream:
            for descriptor in (0, 1, 2):
                os.dup2(stream.fileno(), descriptor)
        put(path / (side + ".watcher"), json.dumps({"pid": os.getpid(), "pgid": os.getpgrp()}))
        os.write(ready, b"1")
        os.close(ready)
        # Kernel parentage cannot be fooled by reuse of the original agent PID.
        while os.getppid() == parent:
            time.sleep(0.1)
    finally:
        try:
            abort(path)
        finally:
            os._exit(0)


def start_watcher(path, side):
    parent = os.getpid()
    reader, writer = os.pipe()
    try:
        if os.fork() == 0:
            os.close(reader)
            watch_parent(path, side, parent, writer)
        os.close(writer)
        writer = None
        require(select.select([reader], [], [], 5)[0] and os.read(reader, 1) == b"1",
                "parent-death watchdog did not detach")
    finally:
        os.close(reader)
        if writer is not None:
            os.close(writer)


def pane(args):
    path, meta = load(args)
    sid, _, _ = pinned_pane(path, meta, args.side)
    with locked(path):
        active(path)
        require(read_json(path / "pair.json")[args.side] == os.environ["AGTERM_PANE_ID"].lower(),
                "launch pane mismatch")
        put(path / (args.side + ".starting"), sid)
        try:
            live_pair(path, meta)
            require((path / (args.side + ".txt")).is_file(), "packet already consumed; stop")
        except Exception:
            abort_locked(path)
            raise
    try:
        spec = meta[args.side]
        os.chdir(meta["cwd"])
        argv = [spec["executable"], "--model", spec["model"]]
        if spec["kind"] == "opencode":
            argv.append("--prompt")
        argv.append(bootstrap(args))
        with locked(path):
            active(path)
            expected = live_pair(path, meta)
            record = {"session": sid.lower(), "panes": expected, "pid": os.getpid(),
                      "birth": process_identity(os.getpid())[1]}
            put(path / (args.side + ".launched"), json.dumps(record))
        # Fork without a request lock: the watcher must not inherit an owned flock.
        start_watcher(path, args.side)
        with locked(path):
            active(path)
            live_pair(path, meta)
            os.execv(argv[0], argv)
    except (Exception, KeyboardInterrupt) as error:
        abort(path)
        put(path / (args.side + ".error"), type(error).__name__)
        raise RuntimeError(f"created={sid} side={args.side}; startup failed; "
                           "pending delivery invalidated; delivery ambiguous; never repeat") from error


def consume(args):
    path, meta = load(args)
    with locked(path):
        consume_locked(args, path, meta)


def consume_locked(args, path, meta):
    sid, window, socket, _ = owner(path, meta, args.side)
    packet = path / (args.side + ".txt")
    private(packet)
    # The permanent claim survives crashes; a lost delivery is safer than replay.
    put(path / (args.side + ".consumed"), sid)
    try:
        body = packet.read_bytes().decode("utf-8")
    finally:
        packet.unlink()
    other = "right" if args.side == "left" else "left"
    env = ["env", f"AGTERM_SESSION_ID={sid}", f"AGTERM_WINDOW_ID={window}",
           f"AGTERM_SOCKET={socket}", f"AGTERM_PANE={args.side}",
            f"AGTERM_PANE_ID={os.environ['AGTERM_PANE_ID']}",
            f"AGTERMCTL={SCRIPT}", "AGTERM_NEXT_RELAY=1",
            f"AGTERM_NEXT_STATE={path.parent}", f"AGTERM_NEXT_REQUEST={args.request}",
            f"PEER_CHAT_CONFIG={meta['peer_config']}"]
    command = shlex.join([*env, meta["peer_chat"], "--to", meta[other]["profile"],
                          "--session", sid, "--window", window, "--pane", other, "--stdin"])
    prefix = (f"You are the stable {args.side} pane. Your {other} peer is {meta[other]['profile']}.\n"
              f"For EVERY peer-chat tool call use this explicit environment/address command:\n{command}\n"
              "Pipe only your message to stdin. Do not infer addresses from cwd or active selection.\n"
              "If pane roles change, stop. Never auto-answer trust/auth/permission prompts.\n")
    sys.stdout.write(prefix + body)
    sys.stdout.flush()


def relay():
    """Let existing peer-chat call this executable with its socket pinned."""
    args = argparse.Namespace(state_dir=os.environ["AGTERM_NEXT_STATE"],
                              request=os.environ["AGTERM_NEXT_REQUEST"])
    path, meta = load(args)
    with locked(path):
        return relay_locked(path, meta)


def relay_locked(path, meta):
    side = os.environ["AGTERM_PANE"]
    sid, _, _, expected = owner(path, meta, side)
    forwarded = []
    incoming = iter(sys.argv[1:])
    for arg in incoming:
        if arg in ("--socket", "--window"):
            require(next(incoming, None) == meta[arg[2:]], "relay address mismatch")
        else:
            require(not arg.startswith(("--socket=", "--window=")), "use separate address arguments")
            forwarded.append(arg)
    command = tuple(forwarded[:2])
    require(forwarded[:1] in (["tree"], ["version"]) or command in
            (("window", "list"), ("session", "text"), ("surface", "cursor"), ("session", "type")),
            "unsupported peer-chat control command")
    if command == ("session", "type"):
        other = "right" if side == "left" else "left"
        pins = {"--target": sid, "--pane": other, "--expect-pane-id": expected[other]}
        options = iter(forwarded[2:])
        switches = []
        for option in options:
            if option in pins:
                require(next(options, "").lower() == pins[option].lower(), "type target/pane override conflict")
            else:
                require(option in ("--stdin", "--json"), "type requires guarded stdin delivery")
                switches.append(option)
        require("--stdin" in switches, "type requires stdin")
        forwarded = ["session", "type", *switches, *(part for item in pins.items() for part in item)]
    return subprocess.run(addressed(meta, forwarded), timeout=30, check=False).returncode


def start(args):
    payload = read_json(Path(args.payload))
    require(isinstance(payload, dict) and set(payload) == {"prompt", "cwd", "left", "right"},
            "payload keys must be prompt, cwd, left, right")
    text(payload["prompt"])
    cwd = Path(text(payload["cwd"]))
    require(cwd.is_absolute() and cwd.is_dir(), "explicit existing absolute cwd required")
    meta = {"cwd": str(cwd.resolve()), "source": identity(args.source),
            "window": identity(args.window), "socket": text(args.socket),
            "ctl": executable(args.agtermctl), "timeout": args.timeout,
            "peer_chat": executable(args.peer_chat),
            "peer_config": str(Path(args.peer_config).resolve())}
    require(Path(meta["socket"]).is_absolute(), "absolute explicit socket required")
    require(os.access(SCRIPT, os.X_OK), "launcher must be executable for peer-chat relay")
    process_identity(os.getpid())
    config = read_json(Path(meta["peer_config"]))
    agents = config.get("agents", {})
    for side in SIDES:
        spec = payload[side]
        require(isinstance(spec, dict) and set(spec) == {"kind", "profile", "model", "executable"},
                "side keys must be kind, profile, model, executable; no extra CLI flags")
        require(spec["kind"] in ("claude", "codex", "opencode"), "unknown agent kind")
        text(spec["model"])
        require(not spec["model"].startswith("-"), "invalid model")
        profile = agents.get(text(spec["profile"]), {})
        require(profile.get("kind") == spec["kind"], "peer-chat profile kind mismatch")
        command = text(profile.get("command"))
        require(Path(command).name == command and not any(c.isspace() for c in command),
                "peer-chat command must be a process basename")
        meta[side] = dict(spec, executable=executable(spec["executable"]), command=command)
    capabilities = ctl(meta, "version").get("app", {}).get("capabilities", [])
    for capability in ("session.new.split-command", "session.type.expect-pane-id"):
        require(capability in capabilities, f"server lacks {capability} capability")
    windows = ctl(meta, "window", "list").get("windows", [])
    require(any(w.get("id", "").lower() == meta["window"].lower() and w.get("open") is True
                for w in windows), "exact open window required")
    node = session(ctl(meta, "tree"), meta["source"])
    require(node.get("hasSplit") is True, "source must have a live split, including hidden splits")
    meta["source_panes"] = pane_ids(node)
    for side, field in zip(SIDES, ("foreground", "splitForeground")):
        argv = node.get(field)
        require(runs(argv, meta[side]["command"]), f"source {side} program mismatch/unknown")
    root = Path(args.state_dir).absolute()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = request_path(root, args.request)
    path.mkdir(mode=0o700)  # Never reuse an ID, including after ambiguous create failure.
    put(path / "lock", "")
    put(path / "meta.json", json.dumps(meta))
    put(path / "left.txt", "You are the only writer. Collaborate with the right read-only peer.\n\n" + payload["prompt"])
    put(path / "right.txt", "Read-only peer: acknowledge locally, then STOP and wait for a message from left. "
        "Do not independently implement, edit files, or initiate the task.\n")
    # Ghostty's login-style argv[0] breaks relocatable Python; env restores it.
    commands = [shlex.join(["/usr/bin/env", sys.executable, SCRIPT, "pane", "--state-dir", str(root),
                           "--request", args.request, "--side", side]) for side in SIDES]
    created = None
    stage = "create (ID unknown; do not retry)"
    try:
        result = ctl(meta, "session", "new", "--after", meta["source"], "--cwd", meta["cwd"],
                     "--no-select", "--command", commands[0], "--split-command", commands[1])
        created = identity(result.get("id"))
        require(created.lower() != meta["source"].lower(), "server returned source session")
        put(path / "created", created)
        stage = "left/right structure"
        node = session(ctl(meta, "tree"), created)
        require(node.get("hasSplit") is True and node.get("split") is True,
                "new visible pair not verified")
        require(node.get("cwd") == meta["cwd"] and node.get("splitCwd") == meta["cwd"],
                 "new pair cwd not verified")
        with locked(path):
            active(path)
            put(path / "pair.json", json.dumps(pane_ids(node)))
        status = {}
        for inspection in range(args.inspections):
            active(path)
            if inspection:
                time.sleep(0.25)
                node = session(ctl(meta, "tree"), created)
            status = {side: ("process observed; composer readiness unverified" if
                            runs(node.get(field), meta[side]["command"]) else "startup unverified")
                      for side, field in zip(SIDES, ("foreground", "splitForeground"))}
            if all(v.startswith("process") for v in status.values()):
                break
        missing = [side for side in SIDES if not status[side].startswith("process")]
        stage = "/".join(missing)
        require(not missing, "agent process startup unverified")
        stage = "left selection"
        with locked(path):
            active(path)
            live_pair(path, meta)
            ctl(meta, "session", "select", "--target", created)
            ctl(meta, "session", "focus", "left", "--target", created)
        print(json.dumps({"id": created, "request": args.request, "panes": status}))
    except (Exception, KeyboardInterrupt) as error:
        abort(path)
        put(path / "error.json", json.dumps({"created": created, "side": stage,
                                             "error": type(error).__name__}))
        raise RuntimeError(f"request={args.request} created={created or 'UNKNOWN'} side={stage}; "
                           f"pending delivery invalidated; delivery ambiguous; no retry and no closes; {error}") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    launch = sub.add_parser("start", help="create once using a confirmed JSON payload",
                            description="Payload models must be explicitly confirmed. The launcher cannot "
                            "auto-verify runtime models: source startup argv may be stale after model changes.")
    launch.add_argument("--payload", required=True)
    for flag in ("source", "window", "socket"):
        launch.add_argument("--" + flag, required=True)
    launch.add_argument("--agtermctl", default=os.environ.get("AGTERMCTL", "agtermctl"))
    launch.add_argument("--peer-chat", required=True)
    launch.add_argument("--peer-config", required=True)
    launch.add_argument("--timeout", type=float, default=5,
                        help="per-control-call timeout in seconds (default: 5; maximum: 30)")
    launch.add_argument("--inspections", type=int, default=120,
                        help="startup process inspections, 0.25s apart (default: 120; range: 1-240); "
                        "process observation does not establish composer readiness")
    for name in ("pane", "consume"):
        child = sub.add_parser(name)
        child.add_argument("--side", choices=SIDES, required=True)
    for child in (launch, sub.choices["pane"], sub.choices["consume"]):
        child.add_argument("--state-dir", required=True)
        child.add_argument("--request", required=True)
    args = parser.parse_args()
    try:
        if args.mode == "start":
            require(0 < args.timeout <= 30 and 1 <= args.inspections <= 240, "invalid bounds")
        {"start": start, "pane": pane, "consume": consume}[args.mode](args)
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"agterm-next: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if os.environ.get("AGTERM_NEXT_RELAY") == "1" and sys.argv[1:2] not in (
            ["start"], ["pane"], ["consume"]):
        sys.exit(relay())
    sys.exit(main())
