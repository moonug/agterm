#!/usr/bin/env python3
"""Offline tests for the peer-chat deferred delivery queue.

Every process runs against a fake agtermctl (fixtures/fake-agtermctl.py) whose
conditions live in a state file, and against a private queue directory. No
test touches a live agterm pane; the real ctl binary is never invoked.
"""

import fcntl
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "peer-chat.py")
FAKE_CTL = os.path.join(HERE, "fixtures", "fake-agtermctl.py")
FAKE_CONFIG = os.path.join(HERE, "fixtures", "peer-chat.json")

spec = importlib.util.spec_from_file_location("peer_chat", SCRIPT)
pc = importlib.util.module_from_spec(spec)
sys.modules["peer_chat"] = pc
spec.loader.exec_module(pc)

SOCKET_NAME = "agterm.sock"
PANE_TOKEN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SPLIT_TOKEN = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def initial_state(socket_path):
    return {
        "socket": socket_path,
        "capabilities": ["session.type.pane-id"],
        "windows": [{"id": "win", "open": True, "active": True}],
        "sessions": [
            {
                "id": "sid",
                "hasSplit": True,
                "foreground": ["opencode"],
                "splitForeground": ["zsh"],
                "paneID": PANE_TOKEN,
                "splitPaneID": SPLIT_TOKEN,
                "cwd": "/tmp",
            }
        ],
        "composer": "empty",
        "buffer": "",
        "events": [],
        "submitted": 0,
        "submit_ok": True,
        "type_delay": 0,
    }


class QueueWorld:
    """Temporary queue dir, fake socket file, fake ctl and state file."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="peer-chat-queue-test-"))
        os.chmod(self.root, 0o700)
        self.queue_dir = self.root / "queue"
        self.queue_dir.mkdir(mode=0o700)
        self.state_path = self.root / "state.json"
        self.socket_path = self.root / SOCKET_NAME
        with open(self.socket_path, "w") as fh:
            fh.write("")
        self.state = initial_state(str(self.socket_path))
        self.write_state()

    def write_state(self):
        tmp = self.state_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh)
        os.replace(tmp, self.state_path)

    def read_state(self):
        with open(self.state_path, encoding="utf-8") as fh:
            return json.load(fh)

    def base_env(self):
        env = dict(os.environ)
        env.update(
            {
                "AGTERMCTL": FAKE_CTL,
                "PEER_CHAT_SOCKET": str(self.socket_path),
                "PEER_CHAT_QUEUE_DIR": str(self.queue_dir),
                "PEER_CHAT_CONFIG": FAKE_CONFIG,
                "AGTERM_SESSION_ID": "sid",
                "AGTERM_PANE": "left",
                "AGTERM_WINDOW_ID": "win",
                "PEER_CHAT_FAKE_STATE": str(self.state_path),
                "PEER_CHAT_DEBUG_DELIVERY": str(self.root / "delivery-debug.log"),
                "PEER_CHAT_FAKE_TYPELOG": str(self.root / "type-debug.log"),
            }
        )
        for var in ("AGTERM_STATE_DIR", "PEER_CHAT_WORKER_LOCK_FD"):
            env.pop(var, None)
        return env

    def debug_text(self, suffix=""):
        parts = []
        for name, label in (
            ("delivery-debug.log", "delivery"),
            ("type-debug.log", "type"),
        ):
            path = self.root / name
            if path.exists():
                parts.append(f"{label} trace{suffix}:\n{path.read_text()[-2000:]}")
        return "\n".join(parts) or f"no debug trace{suffix}"

    def run_cli(self, args, timeout=60, stdin_text=None, env=None):
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin_text,
            env=env or self.base_env(),
        )

    def connect(self):
        os.environ["PEER_CHAT_QUEUE_DIR"] = str(self.queue_dir)
        return pc.queue_connect(create=True)

    def events(self):
        return self.read_state().get("events", [])

    def submitted(self):
        return self.read_state().get("submitted", 0)

    def set_composer(self, composer):
        self.state["composer"] = composer
        self.write_state()

    def set_pane_token(self, token):
        self.state["sessions"][0]["paneID"] = token
        self.write_state()

    def replace_socket_file(self):
        tmp = self.root / (SOCKET_NAME + ".new")
        with open(tmp, "w") as fh:
            fh.write("replaced")
        os.replace(tmp, self.socket_path)

    def target_locks_free(self, directory=None):
        """Every delivery target lock is releasable right now."""
        directory = directory or self.queue_dir
        for path in sorted(directory.glob("target-*.lock")):
            fd = os.open(str(path), os.O_RDWR | os.O_CLOEXEC)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                return False
            finally:
                os.close(fd)
        return True

    def cleanup(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)


def wait_until(predicate, timeout=15.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class QueueStoreTests(unittest.TestCase):
    """In-process store behaviour with the worker spawn patched out."""

    def setUp(self):
        self.world = QueueWorld()
        spawned = []
        self.spawned = spawned
        self._patch = pc.ensure_queue_worker
        pc.ensure_queue_worker = lambda directory: spawned.append("x") or "started"
        self.conn = self.world.connect()

    def tearDown(self):
        pc.ensure_queue_worker = self._patch
        self.conn.close()
        self.world.cleanup()

    def profile(self, pane="left"):
        return pc.Profile(
            agent="opencode",
            command="opencode",
            label="Chat from Claude: ",
            submit="\n",
            pane=pane,
        )

    def enqueue(self, body="hello", **kwargs):
        defaults = dict(
            target_name="opencode",
            window="win",
            sid="sid",
            pane_id=PANE_TOKEN,
            socket_path=str(self.world.socket_path),
            fingerprint=(1, 2, 3),
            agtermctl_path="/usr/bin/agtermctl",
            ttl_seconds=1800,
            delivery_id=None,
        )
        defaults.update(kwargs)
        record, worker = pc.enqueue_message(
            self.conn, self.world.queue_dir, body, self.profile(), **defaults
        )
        return record, worker

    def test_enqueue_record_and_spawn(self):
        record, worker = self.enqueue()
        self.assertEqual(record["status"], "pending")
        self.assertEqual(worker, "started")
        self.assertEqual(self.spawned, ["x"])
        row = self.conn.execute(
            "SELECT body, pane_id, submit FROM queue WHERE id = ?",
            (record["id"],),
        ).fetchone()
        self.assertIn("hello", row["body"])
        self.assertEqual(row["pane_id"], PANE_TOKEN)

    def test_delivery_id_is_idempotent_and_conflicting(self):
        first, _ = self.enqueue(delivery_id="11111111-1111-1111-1111-111111111111")
        again, _ = self.enqueue(
            body="hello",
            delivery_id="11111111-1111-1111-1111-111111111111",
        )
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(len(self.spawned), 1)  # no spawn on the repeat
        with self.assertRaises(ValueError):
            self.enqueue(body="other", delivery_id=first["id"])

    def test_queue_cap_refuses_after_the_limit(self):
        for index in range(pc.QUEUE_MAX_PENDING):
            self.enqueue(body=f"m{index}")
        with self.assertRaises(RuntimeError):
            self.enqueue(body="overflow")

    def test_cancel_only_pending_and_body_is_gone(self):
        record, _ = self.enqueue()
        receipt = pc.queue_cancel(self.conn, record["id"])
        self.assertEqual(receipt, {"cancelled": record["id"]})
        row = self.conn.execute(
            "SELECT status, body FROM queue WHERE id = ?", (record["id"],)
        ).fetchone()
        self.assertEqual(row["status"], "cancelled")
        self.assertIsNone(row["body"])
        with self.assertRaises(RuntimeError):
            pc.queue_cancel(self.conn, record["id"])

    def test_status_never_contains_the_body(self):
        secret = "КВИНТЭССЕНЦИЯ secret body text"
        record, worker = self.enqueue(body=f"Chat from Claude: {secret}")
        status = pc.queue_status(self.conn, None)
        self.assertNotIn(secret, json.dumps(status))
        single = pc.queue_status(self.conn, record["id"])
        self.assertNotIn(secret, json.dumps(single))
        self.assertEqual(single["records"]["status"], "pending")
        self.assertIn(worker, ("started", "running"))

    def test_terminal_metadata_is_purged_after_retention(self):
        record, _ = self.enqueue()
        pc.queue_cancel(self.conn, record["id"])
        old = time.time() - pc.METADATA_RETENTION_SECONDS - 10
        self.conn.execute(
            "UPDATE queue SET updated_at = ? WHERE id = ?", (old, record["id"])
        )
        self.conn.commit()
        pc.queue_status(self.conn, None)
        row = self.conn.execute(
            "SELECT * FROM queue WHERE id = ?", (record["id"],)
        ).fetchone()
        self.assertIsNone(row)

    def test_classify_delivery_errors(self):
        cases = [
            (
                pc.PromptBlocked("dialog", "permission_dialog"),
                ("pending", "permission_dialog"),
            ),
            (pc.PromptBlocked("gone", "pane_gone"), ("failed", "pane_gone")),
            (
                pc.DeliveryAmbiguous("ambiguous"),
                ("uncertain", "ambiguous_submit"),
            ),
            (
                pc.ComposerDirty("dirty", "x", cleared=False),
                ("failed", "cleanup_failed"),
            ),
            (KeyboardInterrupt(), ("uncertain", "interrupted")),
            (RuntimeError("boom"), ("failed", "delivery_error")),
        ]
        for err, wanted in cases:
            self.assertEqual(pc.classify_delivery_error(err), wanted)

    def test_sync_guard_refuses_when_pending_records_exist(self):
        self.enqueue()
        with self.assertRaises(RuntimeError) as ctx:
            pc.guard_sync_target(self.conn, "sid", "left")
        self.assertIn("--defer", str(ctx.exception))

    def test_sync_guard_allows_when_nothing_pending(self):
        pc.guard_sync_target(self.conn, "sid", "left")

    def test_target_lock_excludes_concurrent_delivery(self):
        fd = pc.acquire_target_lock(
            self.world.queue_dir, "sid", "left", blocking=False
        )
        self.assertIsNotNone(fd)
        try:
            self.assertIsNone(
                pc.acquire_target_lock(
                    self.world.queue_dir, "sid", "left", blocking=False
                )
            )
        finally:
            pc.release_target_lock(fd)
        self.assertIsNotNone(
            pc.acquire_target_lock(
                self.world.queue_dir, "sid", "left", blocking=False
            )
        )


class WorkerDeliveryTests(unittest.TestCase):
    """Full CLI + worker subprocess paths over the fake ctl."""

    def setUp(self):
        self.world = QueueWorld()
        self.bare_workers = []
        self.bare_worker_lock_busy = False

    def tearDown(self):
        # Detached workers spawned by the CLI are not tracked, so drain the
        # queue: a worker with no pending records exits within a poll cycle.
        conn = self.world.connect()
        try:
            conn.execute(
                "UPDATE queue SET status='cancelled', body=NULL "
                "WHERE status IN ('pending','delivering')"
            )
            conn.commit()
        finally:
            conn.close()
        for proc in self.bare_workers:
            if proc.poll() is None:
                proc.terminate()
        for proc in self.bare_workers:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        wait_until(
            lambda: not pc.worker_alive(self.world.queue_dir), timeout=15
        )
        self.world.cleanup()

    def queue_row(self, record_id):
        conn = self.world.connect()
        try:
            return conn.execute(
                "SELECT * FROM queue WHERE id = ?", (record_id,)
            ).fetchone()
        finally:
            conn.close()

    def defer(self, body):
        sent = self.world.run_cli(
            ["--to", "opencode", "--defer", "--ttl", "30", "--stdin"],
            stdin_text=body,
        )
        self.assertEqual(sent.returncode, 0, sent.stderr)
        receipt = json.loads(sent.stdout)
        self.assertIn("queued", receipt)
        self.assertNotIn("sent", receipt)
        return receipt

    def start_bare_worker(self):
        lock_path = self.world.queue_dir / pc.WORKER_LOCK_NAME
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.bare_worker_lock_busy = True
            os.close(fd)
            return None
        env = self.world.base_env()
        env["PEER_CHAT_WORKER_LOCK_FD"] = str(fd)
        err_file = open(self.world.root / "bare-worker-stderr.txt", "a")
        proc = subprocess.Popen(
            [sys.executable, SCRIPT, pc.WORKER_FLAG],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err_file,
            start_new_session=True,
            pass_fds=(fd,),
        )
        err_file.close()
        os.close(fd)
        self.bare_workers.append(proc)
        return proc

    def insert_pending_row(self, body):
        """Insert one pending record without spawning any worker."""
        st = os.stat(self.world.socket_path)
        profile = pc.Profile(
            agent="opencode",
            command="opencode",
            label="Chat from Claude: ",
            submit="\n",
            pane="left",
        )
        real_ensure = pc.ensure_queue_worker
        pc.ensure_queue_worker = lambda directory: "suppressed"
        try:
            conn = self.world.connect()
            try:
                record, _ = pc.enqueue_message(
                    conn,
                    self.world.queue_dir,
                    f"Chat from Claude: {body}",
                    profile,
                    "opencode",
                    "win",
                    "sid",
                    PANE_TOKEN,
                    str(self.world.socket_path),
                    (st.st_dev, st.st_ino, st.st_ctime_ns),
                    FAKE_CTL,
                    1800,
                    None,
                )
            finally:
                conn.close()
        finally:
            pc.ensure_queue_worker = real_ensure
        return record

    def test_dialog_gate_waits_then_delivers_exactly_once(self):
        self.world.set_composer("dialog")
        receipt = self.defer("ping through the dialog gate")
        # the worker must probe the blocked head without typing into it
        self.assertTrue(
            wait_until(
                lambda: self.queue_row(receipt["id"])["attempts"] >= 1,
                timeout=20,
            ),
            "worker never probed the blocked head",
        )
        self.assertEqual(self.world.events(), [])
        self.world.set_composer("empty")
        if not wait_until(
            lambda: self.queue_row(receipt["id"])["status"] == "sent",
            timeout=20,
        ):
            row = self.queue_row(receipt["id"])
            self.fail(
                f"record ended {row['status']}/{row['last_block']}; "
                + self.world.debug_text(
                    f" (events={self.world.events()[:5]})"
                )
            )
        self.assertEqual(self.world.submitted(), 1)
        self.assertTrue(self.world.events())
        self.assertIsNone(self.queue_row(receipt["id"])["body"])

    def test_pane_id_mismatch_fails_without_typing(self):
        receipt = self.defer("ping a replaced pane")
        # the pane is replaced after the pin, before the worker acts
        self.world.set_pane_token("cccccccc-cccc-cccc-cccc-cccccccccccc")
        self.assertTrue(
            wait_until(
                lambda: self.queue_row(receipt["id"])["status"] == "failed",
                timeout=20,
            ),
        )
        row = self.queue_row(receipt["id"])
        self.assertEqual(row["last_block"], "pane_replaced")
        self.assertEqual(self.world.events(), [])

    def test_socket_replacement_fails_without_typing(self):
        self.world.set_composer("dialog")
        receipt = self.defer("ping a restarted server")
        wait_until(
            lambda: self.queue_row(receipt["id"])["attempts"] >= 1, timeout=20
        )
        self.world.replace_socket_file()
        self.world.set_composer("empty")
        self.assertTrue(
            wait_until(
                lambda: self.queue_row(receipt["id"])["status"] == "failed",
                timeout=20,
            ),
        )
        row = self.queue_row(receipt["id"])
        self.assertIn(row["last_block"], ("socket_replaced", "socket_gone"))
        self.assertEqual(self.world.events(), [])

    def test_ttl_expiry_marks_expired_without_typing(self):
        self.world.set_composer("dialog")  # keep the worker from delivering
        receipt = self.defer("never delivered in time")
        conn = self.world.connect()
        try:
            conn.execute(
                "UPDATE queue SET deadline = ? WHERE id = ?",
                (time.time() - 1, receipt["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        self.assertTrue(
            wait_until(
                lambda: self.queue_row(receipt["id"])["status"] == "expired",
                timeout=20,
            ),
        )
        self.assertEqual(self.world.events(), [])

    def test_capability_requirement_refuses_defer(self):
        self.world.state["capabilities"] = []
        self.world.write_state()
        sent = self.world.run_cli(
            ["--to", "opencode", "--defer", "--stdin"],
            stdin_text="no pane-id support",
        )
        self.assertNotEqual(sent.returncode, 0)
        self.assertIn("session.type.pane-id", sent.stderr)
        self.assertEqual(self.world.events(), [])
        self.assertNotIn("queued", sent.stdout)

    def test_defer_without_ttl_uses_the_default(self):
        self.world.set_composer("dialog")  # the worker only probes
        sent = self.world.run_cli(
            ["--to", "opencode", "--defer", "--stdin"],
            stdin_text="default ttl check",
        )
        self.assertEqual(sent.returncode, 0, sent.stderr)
        receipt = json.loads(sent.stdout)
        row = self.queue_row(receipt["id"])
        self.assertEqual(
            row["deadline"] - row["created_at"], pc.DEFAULT_TTL_SECONDS
        )

    def test_sync_send_creates_queue_home_and_releases_the_lock(self):
        fresh_dir = self.world.root / "queue-fresh"
        env = self.world.base_env()
        env["PEER_CHAT_QUEUE_DIR"] = str(fresh_dir)
        self.assertFalse(fresh_dir.exists())
        sent = self.world.run_cli(
            ["--to", "opencode", "--stdin"],
            stdin_text="direct hello",
            env=env,
        )
        self.assertEqual(sent.returncode, 0, sent.stderr)
        self.assertIn('"sent"', sent.stdout)
        self.assertTrue(fresh_dir.exists())
        self.assertEqual(self.world.submitted(), 1)
        self.assertTrue(self.world.target_locks_free(fresh_dir))

    def test_sync_send_failure_still_releases_the_lock(self):
        self.world.set_composer("dialog")  # final refusal, no retry loop
        sent = self.world.run_cli(
            ["--to", "opencode", "--stdin"], stdin_text="direct refusal"
        )
        self.assertNotEqual(sent.returncode, 0)
        self.assertIn("permission dialog", sent.stderr)
        self.assertEqual(self.world.submitted(), 0)
        self.assertTrue(self.world.target_locks_free())

    def test_sender_death_does_not_stop_an_accepted_worker(self):
        proc = subprocess.Popen(
            [
                sys.executable,
                SCRIPT,
                "--to",
                "opencode",
                "--defer",
                "--ttl",
                "30",
                "--stdin",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=self.world.base_env(),
        )
        stdout, _ = proc.communicate(input="survives the sender", timeout=60)
        receipt = json.loads(stdout)
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # the fast enqueue already exited; the worker is on its own
        proc.wait()
        self.assertTrue(
            wait_until(
                lambda: self.queue_row(receipt["id"])["status"] == "sent",
                timeout=20,
            ),
        )
        self.assertEqual(self.world.submitted(), 1)

    def test_worker_death_in_delivering_becomes_uncertain_without_retry(self):
        self.world.state["type_delay"] = 1.5
        self.world.write_state()
        record = self.insert_pending_row("killed mid delivery " * 20)
        victim = self.start_bare_worker()
        self.assertIsNotNone(victim, "lifetime lock unexpectedly busy")
        self.assertTrue(
            wait_until(lambda: len(self.world.events()) >= 2, timeout=25),
            "worker never started typing",
        )
        # kill exactly this test's worker, then wait for the kernel to
        # release its lifetime lock
        victim.kill()
        victim.wait(timeout=10)
        row = self.queue_row(record["id"])
        if row["status"] not in ("delivering", "uncertain"):
            self.fail(
                f"killed worker left attempts={row['attempts']} "
                f"{row['status']}/{row['last_block']}; "
                + self.world.debug_text(f" (events={self.world.events()[:6]})")
            )
        wait_until(
            lambda: not pc.worker_alive(self.world.queue_dir), timeout=10
        )
        replacement = self.start_bare_worker()
        self.assertIsNotNone(replacement, "lifetime lock unexpectedly busy")
        if not wait_until(
            lambda: self.queue_row(record["id"])["status"] == "uncertain",
            timeout=20,
        ):
            stderr_path = self.world.root / "bare-worker-stderr.txt"
            self.fail(
                f"replacement left {self.queue_row(record['id'])['status']}; "
                f"stderr: {stderr_path.read_text()[-1500:]!r}; "
                + self.world.debug_text()
            )
        # the replacement must run to completion on its own: no pending
        # records left, so it exits without touching anything again
        self.assertTrue(
            wait_until(
                lambda: replacement.poll() is not None
                and not pc.worker_alive(self.world.queue_dir),
                timeout=20,
            ),
            "replacement worker did not exit after draining the queue",
        )
        self.assertIsNone(self.queue_row(record["id"])["body"])
        events_after = len(self.world.events())
        time.sleep(3)
        self.assertEqual(len(self.world.events()), events_after)
        self.assertEqual(self.world.submitted(), 0)

    def test_sync_send_refused_while_queue_holds_pending(self):
        # the row exists without any worker: the refusal must come from the
        # pending guard, never from racing a live worker's target lock
        record = self.insert_pending_row("queued first")
        sync = self.world.run_cli(
            ["--to", "opencode", "--stdin"], stdin_text="direct second"
        )
        self.assertNotEqual(sync.returncode, 0)
        self.assertIn("deferred message", sync.stderr)
        self.assertEqual(self.world.events(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
