#!/usr/bin/env python3
"""Subprocess-only fixtures: never connect to an app or run a real agent."""

import concurrent.futures
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

SCRIPT = Path(__file__).with_name("agterm-next.py").resolve()
SOURCE = "11111111-1111-4111-8111-111111111111"
NEW = "22222222-2222-4222-8222-222222222222"
WINDOW = "33333333-3333-4333-8333-333333333333"
TOKEN = "44444444-4444-4444-8444-444444444444"
RIGHT_TOKEN = "55555555-5555-4555-8555-555555555555"
BODY = "  Следующий шаг\n'quoted' \"double\" ; $(touch NEVER) `exit` & | < >\n\nконец\n"

FAKE_CTL = r'''
import json, os, signal, sys, time
from pathlib import Path
args = sys.argv[1:]
state = json.loads(Path(os.environ["FAKE_STATE"]).read_text())
with open(os.environ["FAKE_LOG"], "a") as stream:
    stream.write(json.dumps(args) + "\n")
global_command = args[0] == "version" or args[:2] == ["window", "list"]
offset = 1 if args[0] in ("version", "tree") else 3 if args[:2] == ["session", "focus"] else 2
values = {"--socket"} if global_command else {"--socket", "--window"}
switches = {"--json"}
if args[:2] == ["session", "new"]:
    values.update({"--after", "--cwd", "--command", "--split-command"})
    switches.add("--no-select")
elif args[:2] in (["session", "select"], ["session", "focus"], ["session", "type"]):
    values.add("--target")
    if args[:2] == ["session", "type"]:
        values.update({"--pane", "--expect-pane-id"})
        switches.add("--stdin")
options = iter(args[offset:])
for option in options:
    if option in values:
        assert next(options, None) is not None, "missing value: " + option
    elif option not in switches:
        print("Unknown option: " + option, file=sys.stderr)
        sys.exit(64)
assert args.count("--socket") == 1
assert args[args.index("--socket") + 1] == state["socket"]
if not global_command:
    assert args.count("--window") == 1
    assert args[args.index("--window") + 1] == state["window"]
if state.get("fail_command") == args[:offset]:
    print(state.get("diagnostic_stdout", ""))
    print(state["diagnostic_stderr"], file=sys.stderr)
    sys.exit(64)
created = Path(os.environ["FAKE_CREATED"])
if args[:2] == ["session", "new"]:
    created.write_text("0")
    if state.get("interrupt"):
        os.kill(os.getppid(), signal.SIGINT)
        time.sleep(1)
    if state.get("timeout"):
        time.sleep(2)
    if state.get("create_error"):
        sys.exit(3)
    result = {"id": state.get("returned_id", state["new"])}
elif args[0] == "version":
    capabilities = ["session.new.split-command", "session.type.expect-pane-id"]
    if state.get("no_type_guard"):
        capabilities.remove("session.type.expect-pane-id")
    result = {"app": {"capabilities": [] if state.get("old") else capabilities}}
elif args[:2] == ["window", "list"]:
    result = {"windows": [{"id": state["window"], "open": not state.get("closed")} ]}
elif args[0] == "tree":
    source = dict(state["source_node"])
    source["active"] = not created.exists()
    nodes = [] if state.get("source_removed") else [source]
    if created.exists():
        inspections = int(created.read_text())
        created.write_text(str(inspections + 1))
        node = dict(source, id=state["new"], split=True, active=True,
                    paneID=state["left_token"], splitPaneID=state["right_token"])
        node.update(state.get("new_overrides", {}))
        if inspections < state.get("startup_inspections", 0):
            node.pop("foreground", None)
            node.pop("splitForeground", None)
        if state.get("partial"):
            node.pop("splitForeground", None)
        if state.get("bad_cwd"):
            node["splitCwd"] = "/wrong"
        if not state.get("disappeared"):
            nodes.append(node)
    result = {"tree": {"workspaces": [{"sessions": nodes}]}}
elif args[:2] in (["session", "select"], ["session", "focus"]):
    result = {"id": state["new"]}
elif args[:2] == ["session", "type"]:
    assert args[args.index("--target") + 1] == state["new"]
    side = args[args.index("--pane") + 1]
    token = args[args.index("--expect-pane-id") + 1]
    expected = state[side + "_token"]
    if state.get("race_replacement"):
        expected = state["source_node"]["paneID"]
    if token != expected:
        print("pane identity mismatch; no write", file=sys.stderr)
        sys.exit(3)
    Path(os.environ["FAKE_WRITES"]).write_text(sys.stdin.read())
    result = {"id": state["new"]}
else:
    raise AssertionError(args)
print(json.dumps({"ok": True, "result": result}))
'''

FAKE_AGENT = r'''
import concurrent.futures, json, os, shlex, subprocess, sys, time
from pathlib import Path
if os.environ.get("FAKE_REPLAY"):
    command = sys.argv[-1].split("```sh\n", 1)[1].split("\n```", 1)[0]
    sys.exit(subprocess.run(shlex.split(command)).returncode)
record = {
    "argv": sys.argv, "cwd": os.getcwd(), "pid": os.getpid(), "pgid": os.getpgrp(),
    "env": {k: v for k, v in os.environ.items() if k.startswith("AGTERM_")}
}
Path(os.environ["FAKE_AGENT_LOG"]).write_text(json.dumps(record))
directory = Path(os.environ["FAKE_AGENT_DIR"])
(directory / "ready").write_text(json.dumps(record))
def execute(path, request):
    result = subprocess.run(request["argv"], env=dict(os.environ, **request.get("env", {})),
                            input=request.get("input", "").encode(), capture_output=True, timeout=10)
    temporary = path.with_suffix(".tmp-response")
    temporary.write_text(json.dumps({"returncode": result.returncode,
        "stdout": result.stdout.decode(), "stderr": result.stderr.decode()}))
    temporary.rename(path.with_suffix(".response"))
pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
while True:
    for path in directory.glob("*.request"):
        request = json.loads(path.read_text())
        path.unlink()
        if request.get("exit"):
            sys.exit(request["exit"] - 1)
        pool.submit(execute, path, request)
    time.sleep(0.01)
'''


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent pair ' тест ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cwd = self.root / "project space"
        self.cwd.mkdir()
        self.state_dir = self.root / "requests"
        self.payload_file = self.root / "payload.json"
        self.config_file = self.root / "profiles.json"
        self.fake_state = self.root / "fake.json"
        self.log = self.root / "calls.jsonl"
        self.agent_log = self.root / "agent.json"
        self.agents = {}
        self.addCleanup(self.stop_agents)
        self.ctl = self.program("fake-control", FAKE_CTL)
        self.peer = self.program("peer-chat", "raise AssertionError('not to be executed')\n")
        self.env = dict(os.environ, FAKE_STATE=str(self.fake_state), FAKE_LOG=str(self.log),
                        FAKE_CREATED=str(self.root / "fake-created"), FAKE_AGENT_LOG=str(self.agent_log),
                        AGTERM_SESSION_ID=SOURCE, AGTERM_WINDOW_ID=WINDOW,
                        AGTERM_SOCKET=str(self.root / "fake.sock"), AGTERM_PANE="left",
                        AGTERM_PANE_ID=TOKEN, SECRET_TOKEN="not-for-persistence")
        self.env["FAKE_WRITES"] = str(self.root / "typed.txt")
        self.env.pop("AGTERM_NEXT_RELAY", None)
        self.state = {"socket": self.env["AGTERM_SOCKET"], "window": WINDOW, "new": NEW,
                      "left_token": TOKEN, "right_token": RIGHT_TOKEN}
        self.payload = {"prompt": BODY, "cwd": str(self.cwd)}
        self.kinds("claude", "codex")

    def program(self, name, body):
        path = self.root / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o700)
        return path

    def kinds(self, left, right):
        config = {"agents": {}}
        for side, kind in (("left", left), ("right", right)):
            profile = side + "-provider"
            self.payload[side] = {"kind": kind, "profile": profile,
                                  "model": "provider/exact-model", "executable": str(self.program(kind, FAKE_AGENT))}
            config["agents"][profile] = {"kind": kind, "command": kind}
        self.config_file.write_text(json.dumps(config))
        self.state["source_node"] = {"id": SOURCE, "hasSplit": True, "split": False,
                                     "paneID": SOURCE, "splitPaneID": WINDOW,
                                     "cwd": str(self.cwd), "splitCwd": str(self.cwd),
                                     "foreground": [left, "--model", "provider/exact-model", "--resume", "old"],
                                     "splitForeground": [right, "--model", "provider/exact-model"]}

    def call(self, args, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env or self.env,
                              capture_output=True, text=True, timeout=15, check=False)

    def start(self, extra=(), omit=()):
        self.fake_state.write_text(json.dumps(self.state))
        self.payload_file.write_text(json.dumps(self.payload))
        args = ["start", "--payload", str(self.payload_file), "--state-dir", str(self.state_dir),
                "--request", "once", "--source", SOURCE, "--window", WINDOW,
                "--socket", self.state["socket"], "--agtermctl", str(self.ctl),
                "--peer-chat", str(self.peer), "--peer-config", str(self.config_file),
                "--inspections", "1"]
        for flag in omit:
            index = args.index(flag)
            del args[index:index + 2]
        return self.call([*args, *extra])

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def creates(self):
        return [args for args in self.calls() if args[:2] == ["session", "new"]]

    def child(self, mode, side="left", **changes):
        env = dict(self.env, AGTERM_SESSION_ID=NEW, AGTERM_PANE=side,
                   AGTERM_PANE_ID=TOKEN if side == "left" else RIGHT_TOKEN)
        env.update(changes)
        argv = [sys.executable, str(SCRIPT), mode, "--state-dir", str(self.state_dir),
                "--request", "once", "--side", side]
        key = (str(self.state_dir), side)
        if mode == "pane" and key not in self.agents:
            directory = self.root / ("agent-" + uuid.uuid4().hex)
            directory.mkdir()
            env["FAKE_AGENT_DIR"] = str(directory)
            process = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True)
            self.agents[key] = process, directory
            deadline = time.monotonic() + 8
            while not (directory / "ready").exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            if (directory / "ready").exists():
                return subprocess.CompletedProcess(argv, 0, "", "")
            stdout, stderr = process.communicate(timeout=5)
            return subprocess.CompletedProcess(argv, process.returncode, stdout.decode(), stderr.decode())
        if mode == "consume" and key in self.agents and self.agents[key][0].poll() is None:
            return self.agent_call(argv, side, env)
        return self.call(argv[2:], env)

    def agent_call(self, argv, side="left", env=None, input_text=""):
        process, directory = self.agents[(str(self.state_dir), side)]
        self.assertIsNone(process.poll())
        path = directory / (uuid.uuid4().hex + ".request")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"argv": argv, "env": env or {}, "input": input_text}))
        temporary.rename(path)
        response = path.with_suffix(".response")
        deadline = time.monotonic() + 12
        while not response.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        result = json.loads(response.read_text())
        return subprocess.CompletedProcess(argv, **result)

    def stop_agents(self):
        for process, _ in self.agents.values():
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)
        for directory, _ in self.agents:
            self.wait_watchers(Path(directory))

    def wait_watchers(self, state_dir=None):
        for path in (state_dir or self.state_dir).glob("once/*.watcher"):
            pid = json.loads(path.read_text())["pid"]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                status = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "stat="],
                                        capture_output=True, text=True, timeout=2, check=False).stdout.strip()
                if not status or status.startswith("Z"):
                    break
                time.sleep(0.02)
            else:
                self.fail("parent-death watchdog did not exit")

    def relay_environment(self):
        result = self.child("consume")
        self.assertEqual(result.returncode, 0, result.stderr)
        command = next(line for line in result.stdout.splitlines() if line.startswith("env "))
        env = dict(self.env)
        words = shlex.split(command)
        for word in words[1:words.index(str(self.peer))]:
            key, _, value = word.partition("=")
            env[key] = value
        return env

    def update_live_state(self, **changes):
        self.state.update(changes)
        self.fake_state.write_text(json.dumps(self.state))

    def assertStarted(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def assertRejected(self, **kwargs):
        result = self.start(**kwargs)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.creates(), [])

    def test_help(self):
        self.assertEqual(self.call(["--help"]).returncode, 0)
        help_result = self.call(["start", "--help"])
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("cannot auto-verify runtime models", " ".join(help_result.stdout.split()))
        self.assertIn("default: 120", " ".join(help_result.stdout.split()))
        self.assertEqual(self.calls(), [])

    def test_global_and_scoped_address_options(self):
        self.assertStarted()
        for args in self.calls():
            self.assertEqual(args[args.index("--socket") + 1], self.state["socket"])
            if args[0] == "version" or args[:2] == ["window", "list"]:
                self.assertNotIn("--window", args)
            else:
                self.assertEqual(args[args.index("--window") + 1], WINDOW)

    def test_fake_parser_rejects_window_on_global_commands(self):
        self.fake_state.write_text(json.dumps(self.state))
        for command in (["version"], ["window", "list"]):
            result = subprocess.run([str(self.ctl), *command, "--socket", self.state["socket"],
                                     "--json", "--window", WINDOW], env=self.env,
                                    capture_output=True, text=True, timeout=5, check=False)
            self.assertEqual(result.returncode, 64)
            self.assertIn("Unknown option: --window", result.stderr)

    def test_all_kinds_and_same_kind_pairs(self):
        for left in ("claude", "codex", "opencode"):
            for right in ("claude", "codex", "opencode"):
                with self.subTest(left=left, right=right), tempfile.TemporaryDirectory() as isolated:
                    self.state_dir = Path(isolated) / "requests"
                    self.kinds(left, right)
                    self.assertStarted()
                    for side, kind in (("left", left), ("right", right)):
                        result = self.child("pane", side)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        agent = json.loads(self.agent_log.read_text())
                        argv = agent["argv"]
                        self.assertEqual(argv[1:3], ["--model", "provider/exact-model"])
                        self.assertEqual(len(argv), 5 if kind == "opencode" else 4)
                        if kind == "opencode":
                            self.assertEqual(argv[3], "--prompt")
                        self.assertNotIn(BODY, argv[-1])
                        self.assertNotIn(SOURCE, argv[-1])
                        self.assertIn(NEW, argv[-1])
                        self.assertEqual(agent["cwd"], str(self.cwd))
                        self.assertEqual(agent["env"]["AGTERM_PANE"], side)
                    self.stop_agents()
                    self.agents.clear()

    def test_one_create_explicit_targets_and_focus_independence(self):
        self.assertStarted()
        self.assertEqual(len(self.creates()), 1)
        args = self.creates()[0]
        self.assertEqual(args[args.index("--after") + 1], SOURCE)
        self.assertIn("--no-select", args)
        for flag, side in (("--command", "left"), ("--split-command", "right")):
            command = shlex.split(args[args.index(flag) + 1])
            self.assertEqual(command[:2], ["/usr/bin/env", sys.executable])
            self.assertEqual(command[-2:], ["--side", side])
            self.assertNotIn(BODY, " ".join(command))
        self.assertFalse(any("type" in args or "close" in args for args in self.calls()))
        self.assertEqual(self.calls()[-1][:5], ["session", "focus", "left", "--target", NEW])

    def test_exact_body_once_and_private_cleanup(self):
        self.assertStarted()
        for side in ("left", "right"):
            self.assertEqual(self.child("pane", side).returncode, 0)
            result = self.child("consume", side)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("AGTERM_SESSION_ID=" + NEW, result.stdout)
            self.assertNotIn(SOURCE, result.stdout)
            self.assertIn("--pane " + ("right" if side == "left" else "left"), result.stdout)
            if side == "left":
                self.assertTrue(result.stdout.endswith(BODY))
                self.assertIn("right-provider", result.stdout)
            else:
                self.assertNotIn(BODY, result.stdout)
                self.assertIn("acknowledge locally", result.stdout)
            self.assertNotEqual(self.child("consume", side).returncode, 0)
        for path in self.state_dir.rglob("*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
            if path.is_file():
                self.assertNotIn(BODY, path.read_text())
                self.assertNotIn("not-for-persistence", path.read_text())
        self.assertEqual(list(self.state_dir.rglob("*.txt")), [])

    def test_repeat_start_cannot_create_again(self):
        self.assertStarted()
        self.assertNotEqual(self.start().returncode, 0)
        self.assertEqual(len(self.creates()), 1)

    def test_crlf_body_preserved_as_bytes(self):
        self.payload["prompt"] = BODY.replace("\n", "\r\n")
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        result = self.child("consume")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.encode().endswith(self.payload["prompt"].encode()))

    def test_source_pane_cannot_bootstrap(self):
        self.assertStarted()
        result = self.call(["pane", "--state-dir", str(self.state_dir),
                            "--request", "once", "--side", "left"])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.state_dir / "once/left.launched").exists())

    def test_repeat_pane_cannot_restart_agent(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        self.assertNotEqual(self.child("pane").returncode, 0)

    def test_native_bootstrap_replay_cannot_reconsume(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        prompt = json.loads(self.agent_log.read_text())["argv"][-1]
        command = prompt.split("```sh\n", 1)[1].split("\n```", 1)[0]
        for expected in (0, 1):
            result = self.agent_call(shlex.split(command))
            self.assertEqual(result.returncode, expected, result.stderr)
            if expected:
                self.assertNotIn(BODY, result.stdout)

    def test_concurrent_consume_claims_only_once(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.child("consume"), range(2)))
        self.assertEqual(sorted(r.returncode for r in results), [0, 1])
        self.assertEqual(sum(BODY in r.stdout for r in results), 1)

    def test_wrong_session_cannot_launch_or_claim(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        for sid in (SOURCE, TOKEN):
            env = dict(self.env, AGTERM_SESSION_ID=sid)
            result = self.call(["consume", "--state-dir", str(self.state_dir), "--request", "once", "--side", "left"], env)
            self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.state_dir / "once/left.consumed").exists())

    def test_wrong_pane_token_cannot_claim(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        self.assertNotEqual(self.child("consume", AGTERM_PANE_ID=SOURCE).returncode, 0)
        self.assertTrue((self.state_dir / "once/left.txt").exists())

    def test_wrong_window_or_socket_cannot_claim(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        for change in ({"AGTERM_WINDOW_ID": SOURCE}, {"AGTERM_SOCKET": "/wrong"}):
            self.assertNotEqual(self.child("consume", **change).returncode, 0)

    def test_old_server(self):
        self.state["old"] = True
        self.assertRejected()

    def test_missing_atomic_type_capability_prevents_creation(self):
        self.state["no_type_guard"] = True
        self.assertRejected()

    def test_captured_native_argv_before_consume_cannot_replay_with_original_alive(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        captured = json.loads(self.agent_log.read_text())
        env = dict(self.env, **captured["env"], FAKE_REPLAY="1")
        result = subprocess.run(captured["argv"], env=env, capture_output=True,
                                text=True, timeout=5, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a descendant", result.stderr)
        self.assertNotIn(BODY, result.stdout)
        self.assertTrue((self.state_dir / "once/left.txt").exists())
        self.assertEqual(self.child("consume").returncode, 0)

    def test_process_birth_mismatch_rejects_original_descendant(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        path = self.state_dir / "once/left.launched"
        record = json.loads(path.read_text())
        record["birth"] = "different process generation"
        path.write_text(json.dumps(record))
        result = self.child("consume")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("birth changed", result.stderr)
        self.assertFalse((self.state_dir / "once/left.consumed").exists())

    def test_launch_record_binds_both_panes_and_actual_exec_process(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        record = json.loads((self.state_dir / "once/left.launched").read_text())
        self.assertEqual(record["pid"], json.loads(self.agent_log.read_text())["pid"])
        self.assertEqual(record["pid"], self.agents[(str(self.state_dir), "left")][0].pid)
        self.assertEqual(record["panes"], {"left": TOKEN, "right": RIGHT_TOKEN})
        self.assertTrue(record["birth"])

    def test_unrelated_pid_cannot_authorize_consume(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        path = self.state_dir / "once/left.launched"
        record = json.loads(path.read_text())
        record["pid"] = 1
        path.write_text(json.dumps(record))
        result = self.child("consume")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a descendant", result.stderr)

    def test_consume_refuses_swap_promotion_replacement_and_disappearance(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        for changes in ({"paneID": RIGHT_TOKEN, "splitPaneID": TOKEN},
                        {"paneID": RIGHT_TOKEN, "hasSplit": False},
                        {"splitPaneID": SOURCE}, {"paneID": SOURCE}, {"hasSplit": False}):
            with self.subTest(changes=changes):
                self.update_live_state(new_overrides=changes)
                result = self.child("consume")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(BODY, result.stdout)
                self.assertFalse((self.state_dir / "once/left.consumed").exists())
        self.update_live_state(new_overrides={}, disappeared=True)
        self.assertNotEqual(self.child("consume").returncode, 0)
        self.update_live_state(disappeared=False)
        self.assertEqual(self.child("consume").returncode, 0)

    def test_consume_allows_changed_source_identity_after_creation(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        self.state["source_node"]["paneID"] = TOKEN
        self.update_live_state()
        result = self.child("consume")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.endswith(BODY))

    def test_source_removed_after_creation_allows_new_pair_consume_and_relay(self):
        self.assertStarted()
        self.update_live_state(source_removed=True)
        self.assertEqual(self.child("pane").returncode, 0)
        env = self.relay_environment()
        result = self.agent_call([str(SCRIPT), "session", "type", "--stdin"],
                                 env=env, input_text="independent pair")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "typed.txt").read_text(), "independent pair")
        args = self.calls()[-1]
        self.assertEqual(args[args.index("--expect-pane-id") + 1], RIGHT_TOKEN)

    def test_agent_owns_original_foreground_pid_and_watchdog_detaches(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        process, directory = self.agents[(str(self.state_dir), "left")]
        agent = json.loads((directory / "ready").read_text())
        watcher = json.loads((self.state_dir / "once/left.watcher").read_text())
        self.assertEqual(agent["pid"], process.pid)
        self.assertEqual(agent["pgid"], process.pid)
        self.assertEqual(os.getpgid(process.pid), process.pid)
        self.assertEqual(os.getpgid(watcher["pid"]), watcher["pid"])
        self.assertEqual(os.getsid(watcher["pid"]), watcher["pid"])
        self.assertNotEqual(watcher["pgid"], agent["pgid"])
        command = subprocess.run(["/bin/ps", "-p", str(process.pid), "-o", "command="],
                                 capture_output=True, text=True, timeout=2, check=True).stdout
        self.assertIn(self.payload["left"]["executable"], command)
        (directory / "exit.request").write_text(json.dumps({"exit": 1}))
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0)
        self.assertEqual((stdout, stderr), (b"", b""))
        self.wait_watchers()
        self.assertTrue((self.state_dir / "once/aborted").exists())
        self.assertEqual(list((self.state_dir / "once").glob("*.txt")), [])

    def test_agent_exit_aborts_and_erases_both_pending_packets(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        process, directory = self.agents[(str(self.state_dir), "left")]
        (directory / "exit.request").write_text(json.dumps({"exit": 1}))
        _, stderr = process.communicate(timeout=5)
        self.assertEqual(stderr, b"")
        self.wait_watchers()
        self.assertIn("delivery ambiguous", (self.state_dir / "once/aborted").read_text())
        self.assertEqual(list((self.state_dir / "once").glob("*.txt")), [])
        self.assertTrue((self.state_dir / "once/aborted").exists())
        self.assertTrue((self.state_dir / "once/left.launched").exists())
        self.assertNotEqual(self.child("consume").returncode, 0)

    def test_exit_after_delivery_keeps_claim_and_invalidates_pending_peer(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        self.assertEqual(self.child("consume").returncode, 0)
        process, directory = self.agents[(str(self.state_dir), "left")]
        (directory / "exit.request").write_text(json.dumps({"exit": 2}))
        process.communicate(timeout=5)
        self.wait_watchers()
        self.assertTrue((self.state_dir / "once/left.consumed").exists())
        self.assertEqual(list((self.state_dir / "once").glob("*.txt")), [])

    def test_aborted_create_cannot_launch_delayed_agent(self):
        self.state["timeout"] = True
        self.assertNotEqual(self.start(extra=("--timeout", "1")).returncode, 0)
        result = self.child("pane")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("request aborted", result.stderr)
        self.assertFalse(self.agent_log.exists())

    def test_bootstrap_structure_failure_invalidates_pending_packets(self):
        self.assertStarted()
        self.update_live_state(new_overrides={"splitPaneID": SOURCE})
        result = self.child("pane")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.state_dir / "once/aborted").exists())
        self.assertEqual(list((self.state_dir / "once").glob("*.txt")), [])

    def test_missing_explicit_source_even_with_environment(self):
        self.assertRejected(omit=("--source",))

    def test_unknown_source(self):
        self.assertRejected(extra=("--source", TOKEN))

    def test_no_split(self):
        self.state["source_node"]["hasSplit"] = False
        self.assertRejected()

    def test_missing_peer(self):
        self.peer.unlink()
        self.assertRejected()

    def test_missing_program_or_shell_function(self):
        self.payload["left"]["executable"] = "unknown-wrapper-function"
        self.assertRejected()

    def test_missing_model(self):
        self.payload["left"]["model"] = ""
        self.assertRejected()

    def test_confirmed_models_override_stale_source_startup_models(self):
        self.payload["left"]["model"] = "provider/confirmed-current-left"
        self.payload["right"]["model"] = "provider/confirmed-current-right"
        self.state["source_node"]["splitForeground"] = ["codex", "--model=stale-model"]
        self.assertStarted()
        for side in ("left", "right"):
            self.assertEqual(self.child("pane", side).returncode, 0)
            argv = json.loads(self.agent_log.read_text())["argv"]
            self.assertEqual(argv[1:3], ["--model", self.payload[side]["model"]])

    def test_mismatched_source_program(self):
        self.state["source_node"]["foreground"] = ["zsh"]
        self.assertRejected()

    def test_unknown_profile(self):
        self.payload["left"]["profile"] = "unconfirmed"
        self.assertRejected()

    def test_missing_cwd(self):
        self.payload["cwd"] = str(self.root / "missing")
        self.assertRejected()

    def test_unknown_parameters(self):
        self.assertRejected(extra=("--continue",))

    def test_extra_launch_flags(self):
        self.payload["left"]["args"] = ["--dangerously-skip-permissions"]
        self.assertRejected()

    def test_partial_failure_reports_exact_side_and_keeps_evidence(self):
        self.state["partial"] = True
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("created=" + NEW + " side=right", result.stderr)
        self.assertEqual(len(self.creates()), 1)
        self.assertTrue((self.state_dir / "once/error.json").exists())
        self.assertFalse(any("select" in args for args in self.calls()))

    def test_default_inspections_allow_slow_agent_startup(self):
        self.state["startup_inspections"] = 12
        result = self.start(omit=("--inspections",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.creates()), 1)
        self.assertTrue(all(value == "process observed; composer readiness unverified"
                            for value in json.loads(result.stdout)["panes"].values()))

    def test_inspections_maximum_is_accepted(self):
        result = self.start(extra=("--inspections", "240"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_inspections_outside_bounds_do_not_create(self):
        for value in ("0", "241"):
            self.assertRejected(extra=("--inspections", value))

    def test_preflight_stderr_diagnostic_without_source_stdout(self):
        self.state.update(fail_command=["tree"], diagnostic_stdout=BODY,
                          diagnostic_stderr="Error: unable to connect to control socket")
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unable to connect to control socket", result.stderr)
        self.assertIn("exit 64", result.stderr)
        self.assertNotIn(BODY, result.stderr + result.stdout)
        self.assertEqual(self.creates(), [])

    def test_create_stderr_survives_partial_failure_reporting(self):
        self.state.update(fail_command=["session", "new"], diagnostic_stdout=BODY,
                          diagnostic_stderr="Error: unsupported split command\n" + "x" * 3000)
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("created=UNKNOWN", result.stderr)
        self.assertIn("unsupported split command", result.stderr)
        self.assertNotIn("x" * 2001, result.stderr)
        self.assertNotIn(BODY, result.stderr + result.stdout)
        self.assertNotIn(BODY, (self.state_dir / "once/error.json").read_text())
        self.assertEqual(len(self.creates()), 1)

    def test_timeout_is_ambiguous_and_not_retried(self):
        self.state["timeout"] = True
        result = self.start(extra=("--timeout", "1"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("created=UNKNOWN", result.stderr)
        self.assertNotEqual(self.start().returncode, 0)
        self.assertEqual(len(self.creates()), 1)
        self.assertFalse((self.state_dir / "once/left.txt").exists())
        self.assertTrue((self.state_dir / "once/aborted").exists())

    def test_create_error_keeps_guard(self):
        self.state["create_error"] = True
        self.assertNotEqual(self.start().returncode, 0)
        self.assertNotEqual(self.start().returncode, 0)
        self.assertEqual(len(self.creates()), 1)

    def test_interrupt_keeps_ambiguous_evidence(self):
        self.state["interrupt"] = True
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("created=UNKNOWN", result.stderr)
        self.assertEqual(json.loads((self.state_dir / "once/error.json").read_text())["error"],
                         "KeyboardInterrupt")
        self.assertNotEqual(self.start().returncode, 0)
        self.assertEqual(len(self.creates()), 1)

    def test_exec_failure_reports_session_side_and_prevents_restart(self):
        self.assertStarted()
        Path(self.payload["left"]["executable"]).unlink()
        result = self.child("pane")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("created=" + NEW + " side=left", result.stderr)
        self.assertTrue((self.state_dir / "once/left.error").exists())
        self.assertNotEqual(self.child("pane").returncode, 0)

    def test_source_id_returned_by_server_is_rejected(self):
        self.state["returned_id"] = SOURCE
        self.assertNotEqual(self.start().returncode, 0)
        self.assertFalse((self.state_dir / "once/created").exists())

    def test_insecure_state_directory(self):
        self.state_dir.mkdir(mode=0o755)
        self.state_dir.chmod(0o755)  # mkdir mode is masked by the caller's umask
        self.assertRejected()

    def test_relay_pins_socket_for_existing_peer_chat(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        output = self.child("consume").stdout
        command = next(line for line in output.splitlines() if line.startswith("env "))
        env = dict(self.env)
        words = shlex.split(command)
        for word in words[1:words.index(str(self.peer))]:
            key, _, value = word.partition("=")
            env[key] = value
        result = self.agent_call([env["AGTERMCTL"], "tree", "--json", "--window", WINDOW], env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1][-4:], ["--socket", self.state["socket"], "--window", WINDOW])
        for command in (["version"], ["window", "list"]):
            result = self.agent_call([env["AGTERMCTL"], *command, "--json"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("--window", self.calls()[-1])
            self.assertEqual(self.calls()[-1][-2:], ["--socket", self.state["socket"]])

    def test_relay_appends_atomic_expected_peer_token(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        env = self.relay_environment()
        result = self.agent_call([str(SCRIPT), "session", "type", "--stdin", "--target", NEW,
                                  "--pane", "right"], env=env, input_text="peer message")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.calls()[-1]
        self.assertEqual(args[args.index("--expect-pane-id") + 1], RIGHT_TOKEN)
        self.assertEqual((self.root / "typed.txt").read_text(), "peer message")

    def test_relay_atomic_guard_refuses_replacement_after_read(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        env = self.relay_environment()
        self.update_live_state(race_replacement=True)
        result = self.agent_call([str(SCRIPT), "session", "type", "--stdin", "--target", NEW,
                                  "--pane", "right"], env=env, input_text="must not land")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pane identity mismatch", result.stderr)
        self.assertFalse((self.root / "typed.txt").exists())

    def test_relay_refuses_changed_either_pane_before_type(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        env = self.relay_environment()
        for changes in ({"paneID": SOURCE}, {"splitPaneID": SOURCE},
                        {"paneID": RIGHT_TOKEN, "splitPaneID": TOKEN}, {"hasSplit": False}):
            self.update_live_state(new_overrides=changes)
            result = self.agent_call([str(SCRIPT), "session", "type", "--stdin"], env=env)
            self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(args[:2] == ["session", "type"] for args in self.calls()))

    def test_relay_rejects_conflicting_type_overrides(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        env = self.relay_environment()
        for override in (["--target", SOURCE], ["--pane", "left"], ["--expect-pane-id", TOKEN],
                         ["--expect-pane-id=" + TOKEN], ["--target", SOURCE, "--target", NEW],
                         ["--window", SOURCE], ["--socket", "/wrong"]):
            result = self.agent_call([str(SCRIPT), "session", "type", "--stdin", *override], env=env)
            self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(args[:2] == ["session", "type"] for args in self.calls()))

    def test_relay_replayed_outside_original_agent_cannot_type(self):
        self.assertStarted()
        self.assertEqual(self.child("pane").returncode, 0)
        env = self.relay_environment()
        result = subprocess.run([str(SCRIPT), "session", "type", "--stdin"], env=env,
                                capture_output=True, text=True, timeout=5, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a descendant", result.stderr)
        self.assertFalse(any(args[:2] == ["session", "type"] for args in self.calls()))


if __name__ == "__main__":
    unittest.main()
