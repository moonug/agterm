#!/usr/bin/env python3
"""In-memory transport tests for peer-chat.py.

The terminal surface (composer_state, type_text, pane_text, ctl, clock) is
replaced by a linear composer model rendered as an opencode-style box, so the
real clear_composer / type_body / send / send_with_retry run without touching
any live agterm pane. Any attempt to reach the real CLI fails the test.
"""

import importlib.util
import json
import os
import sys
import textwrap
import unittest
from unittest.mock import patch

LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "peer-chat.py")
SOURCE = LOCAL if os.path.exists(LOCAL) else os.path.join(
    os.path.expanduser("~/bin"), "peer-chat.py"
)
spec = importlib.util.spec_from_file_location("peer_chat", SOURCE)
pc = importlib.util.module_from_spec(spec)
sys.modules["peer_chat"] = pc
spec.loader.exec_module(pc)

HERE = os.path.dirname(os.path.abspath(__file__))


def fixture_text(name: str) -> str:
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return fh.read()

PANE_WIDTH = 104
VISIBLE_ROWS = 6


def wrap_rows(text: str) -> list[str]:
    if not text:
        return [""]
    return textwrap.wrap(
        text, PANE_WIDTH, break_long_words=True, break_on_hyphens=False
    )


class FakeTime:
    """Deterministic clock: every read advances by one probe interval."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self) -> float:
        self.now += 0.06
        return self.now

    def sleep(self, seconds: float) -> None:
        pass


class FakePane:
    """A linear composer string rendered as a bordered opencode box."""

    def __init__(self, text: str = "", cap: int | None = None,
                 accept_submit: bool = True):
        self.text = text
        self.cap = cap
        self.accept_submit = accept_submit
        self.events: list[str] = []
        self.submit_count = 0
        self.submitted_text = None

    def render(self) -> str:
        shown = wrap_rows(self.text)[-VISIBLE_ROWS:]
        box = [f"  ┃  {row}" for row in shown]
        box += ["  ┃", "  ┃  Build · Test · high", "  ╹" + "▀" * 70]
        return "\n".join(box)

    def shown(self) -> str:
        return "\n".join(wrap_rows(self.text)[-VISIBLE_ROWS:])

    def type(self, profile, text: str) -> None:
        self.events.append(text)
        if text == profile.submit:
            self.submit_count += 1
            if self.accept_submit:
                self.submitted_text = self.text
                self.text = ""
            return
        if set(text) == {"\x7f"}:
            self.text = self.text[: -len(text)]
            return
        self.text = self.text + text
        if self.cap is not None:
            self.text = self.text[: self.cap]


class Harness:
    """Route the module's terminal calls onto a FakePane."""

    def __init__(self, pane: FakePane):
        self.pane = pane
        self._ctx = None

    def __enter__(self):
        pane = self.pane
        fake_time = FakeTime()

        def no_ctl(*args, **kwargs):
            raise AssertionError("real agtermctl call attempted")

        self._ctx = patch.dict(pc.__dict__, {
            "ctl": no_ctl,
            "pane_text": lambda sid, profile, window=None: pane.render(),
            "_pane_text_unchecked": lambda sid, profile, window=None: pane.render(),
            "composer_state": lambda sid, profile, window=None: (pane.shown(), 5),
            "type_text": lambda sid, profile, text, window=None: pane.type(profile, text),
            "time": fake_time,
        })
        self._ctx.__enter__()
        return self

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)


def opencode_profile() -> "pc.Profile":
    return pc.Profile(
        agent="opencode", command="opencode", label="", submit="\n", pane="left"
    )


def long_message(chars: int) -> str:
    base = (
        "Тест доставки длинного сообщения через peer-chat: чанки, маркеры, "
        "очистка подтверждённо своего текста. КВИНТЭССЕНЦИЯ_МАРКЕР "
    )
    text = (base * (chars // len(base) + 1))[:chars]
    assert "[peer-check" not in text
    return text


class ClearComposerBoundary(unittest.TestCase):
    """clear_composer must scale its deletion budget with the draft length."""

    def make_owned(self, size: int) -> str:
        base = "проверка границы очистки черновика alpha beta gamma delta "
        text = (base * (size // len(base) + 1))[: size - 1] + "!"
        assert "\n" not in text
        return text

    def run_clear(self, size: int):
        owned = self.make_owned(size)
        pane = FakePane(text=owned)
        with Harness(pane):
            ok = pc.clear_composer("sid", opencode_profile(), ("", 2), owned)
        return ok, pane

    def test_197_cleared(self):
        ok, pane = self.run_clear(197)
        self.assertTrue(ok)
        self.assertEqual(pane.text, "")

    def test_2364_cleared(self):
        ok, pane = self.run_clear(2364)
        self.assertTrue(ok)
        self.assertEqual(pane.text, "")

    def test_2365_cleared(self):
        ok, pane = self.run_clear(2365)
        self.assertTrue(ok)
        self.assertEqual(pane.text, "")

    def test_2783_cleared(self):
        ok, pane = self.run_clear(2783)
        self.assertTrue(ok)
        self.assertEqual(pane.text, "")

    def test_5000_cleared(self):
        ok, pane = self.run_clear(5000)
        self.assertTrue(ok)
        self.assertEqual(pane.text, "")


class SendScenarios(unittest.TestCase):
    """Full send/send_with_retry paths over the in-memory terminal."""

    def test_unconfirmable_last_chunk_cleans_and_refuses(self):
        message = long_message(4000)
        pane = FakePane(cap=3900)  # the true tail can never land
        with Harness(pane), self.assertRaises(pc.ComposerDirty) as ctx:
            pc.send_with_retry("sid", opencode_profile(), message)
        err = str(ctx.exception)
        self.assertIn("composer cleared", err)
        self.assertNotIn("cleanup failed", err)
        self.assertNotIn("КВИНТЭССЕНЦИЯ", err)
        self.assertTrue(ctx.exception.cleared)
        self.assertEqual(pane.submit_count, 0)
        self.assertEqual(pane.text, "")

    def test_retry_after_confirmed_cleanup_succeeds_once(self):
        message = long_message(4000)
        pane = FakePane()

        original_type = pane.type

        def flaky_type(profile, text):
            if set(text) != {"\x7f"} and text != profile.submit and pane.loss_armed:
                original_type(profile, text)
                if len(pane.text) > 3900:
                    pane.text = pane.text[:3900]
                    pane.loss_armed = False
                return
            original_type(profile, text)

        pane.loss_armed = True
        pane.type = flaky_type
        with Harness(pane):
            sent = pc.send_with_retry("sid", opencode_profile(), message)
        self.assertEqual(sent, len(message))
        self.assertEqual(pane.submit_count, 1)
        self.assertEqual(pane.text, "")
        self.assertEqual(pane.submitted_text, message)

    def test_foreign_text_blocks_cleanup_and_retry(self):
        message = long_message(3000)
        foreign = " ПОЛЬЗОВАТЕЛЬ_ПИШЕТ_СЮДА"
        pane = FakePane()
        original_type = pane.type
        injected = []

        def injecting_type(profile, text):
            original_type(profile, text)
            if (
                not injected
                and set(text) != {"\x7f"}
                and text != profile.submit
                and len(pane.text) > 1000
            ):
                injected.append(True)
                pane.text += foreign

        pane.type = injecting_type
        with Harness(pane), self.assertRaises(pc.ComposerDirty) as ctx:
            pc.send_with_retry("sid", opencode_profile(), message)
        err = str(ctx.exception)
        self.assertIn("cleanup failed", err)
        self.assertIn("blind deletion refused", err)
        self.assertNotIn("КВИНТЭССЕНЦИЯ", err)
        self.assertFalse(ctx.exception.cleared)
        self.assertEqual(pane.submit_count, 0)
        # Nothing is deleted: the composer still holds the owned prefix and
        # the foreign text, so the user can read what happened in the pane.
        self.assertIn(foreign, pane.text)
        self.assertIn("Тест доставки длинного сообщения", pane.text)

    def test_ambiguous_submit_is_not_retried(self):
        message = long_message(800)
        pane = FakePane(accept_submit=False)
        with Harness(pane), self.assertRaises(pc.DeliveryAmbiguous) as ctx:
            pc.send_with_retry("sid", opencode_profile(), message)
        self.assertIn("do not resend", str(ctx.exception))
        self.assertEqual(pane.submit_count, 1)

    def test_diag_reports_sizes_without_message_text(self):
        message = long_message(4000)
        pane = FakePane(cap=3900)
        with Harness(pane), self.assertRaises(pc.ComposerDirty) as ctx:
            pc.send_with_retry("sid", opencode_profile(), message)
        err = str(ctx.exception)
        self.assertRegex(err, r"chunk \d+/\d+ was typed")
        self.assertIn("expected ~", err)
        self.assertIn("chars in 6 rows", err)
        self.assertNotIn("КВИНТЭССЕНЦИЯ", err)
        # every attempt cleans up provably, so even the final refusal is a
        # confirmed-clear result
        self.assertTrue(ctx.exception.cleared)


class PreWriteRefusals(unittest.TestCase):
    """Occupied or unrecognisable targets must refuse before any event."""

    def test_permission_dialog_blocks_before_any_event(self):
        pane = FakePane()
        pane.render = lambda: fixture_text("opencode-permission-dialog.txt")
        with Harness(pane), self.assertRaises(pc.PromptBlocked) as ctx:
            pc.send_with_retry("sid", opencode_profile(), "ping")
        self.assertEqual(ctx.exception.reason, "permission_dialog")
        self.assertEqual(pane.events, [])
        self.assertEqual(pane.submit_count, 0)
        self.assertIn("nothing was typed", str(ctx.exception))

    def test_foreign_draft_blocks_before_any_event(self):
        pane = FakePane(text="черновик пользователя в панели")
        with Harness(pane), self.assertRaises(pc.PromptBlocked) as ctx:
            pc.send_with_retry("sid", opencode_profile(), "ping")
        self.assertEqual(ctx.exception.reason, "composer_not_empty")
        self.assertEqual(pane.events, [])
        self.assertEqual(pane.submit_count, 0)


class CtlWorld:
    """A ctl-level fake: tree, text, cursor and type over one pane model."""

    def __init__(self, info, socket="/fake/agterm.sock", fingerprint=(1, 2, 3)):
        self.info = info
        self.socket = socket
        self.fingerprint = fingerprint
        self.buffer = ""
        self.type_events = []
        self.submit_count = 0
        self.accept_submit = True
        self.armed_swap = False

    PANE_WIDTH = 104

    def render(self) -> str:
        shown = wrap_rows(self.buffer)[-VISIBLE_ROWS:]
        box = [f"  ┃  {row}" for row in shown]
        box += ["  ┃", "  ┃  Build · Test · high", "  ╹" + "▀" * 70]
        return "\n".join(box)

    def ctl(self, *args, input_text=None):
        if args[:2] == ("tree", "--json"):
            payload = {"result": {"tree": {"workspaces": [{"sessions": [self.info]}]}}}
            return json.dumps(payload)
        if args[:2] == ("session", "text"):
            return self.render()
        if args[:2] == ("surface", "cursor"):
            return "2\n"
        if args[:2] == ("session", "type"):
            assert os.environ.get("PEER_CHAT_SOCKET") == self.socket, (
                "pinned sends must run with the pinned socket env"
            )
            assert os.environ.get("AGTERMCTL") == "agtermctl-under-test"
            self.type_events.append((args, input_text))
            if input_text == "\n":
                self.submit_count += 1
                if self.accept_submit:
                    self.buffer = ""
                return "ok"
            if set(input_text) == {"\x7f"}:
                self.buffer = self.buffer[: -len(input_text)]
                return "ok"
            self.buffer += input_text
            if self.armed_swap:
                self.armed_swap = False
                self.fingerprint = (9, 9, 9)
            return "ok"
        raise AssertionError(f"unexpected ctl call: {args!r}")

    def fake_fingerprint(self, path):
        assert path == self.socket, f"fingerprint probed the wrong socket: {path}"
        return self.fingerprint

    def pinned_profile(self):
        return pc.Profile(
            agent="opencode",
            command="opencode",
            label="",
            submit="\n",
            pane="left",
            window="win",
            session="sid",
            pane_id=self.info["paneID"],
            socket=self.socket,
            socket_fingerprint=self.fingerprint,
        )


class PinnedDelivery(unittest.TestCase):
    """Deferred sends stay bound to the pane they were queued against."""

    def info(self, pane_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"):
        return {
            "id": "sid",
            "hasSplit": True,
            "foreground": ["opencode"],
            "splitForeground": ["zsh"],
            "paneID": pane_id,
            "splitPaneID": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        }

    def run_pinned(self, world, message):
        env = {
            "AGTERMCTL": "agtermctl-under-test",
            "PEER_CHAT_SOCKET": world.socket,
        }
        with patch.dict(pc.__dict__, {
            "ctl": world.ctl,
            "socket_fingerprint": world.fake_fingerprint,
            "time": FakeTime(),
        }), patch.dict(os.environ, env):
            return pc.send_with_retry(
                "sid", world.pinned_profile(), message, window="win"
            )

    def test_type_events_carry_pane_id_and_socket(self):
        world = CtlWorld(self.info())
        sent = self.run_pinned(world, "ping from the deferred queue")
        self.assertEqual(sent, len("ping from the deferred queue"))
        self.assertGreaterEqual(len(world.type_events), 3)
        for args, part in world.type_events:
            self.assertIn("--pane-id", args)
            self.assertEqual(args[args.index("--pane-id") + 1], world.info["paneID"])
        self.assertEqual(world.submit_count, 1)
        self.assertEqual(world.buffer, "")

    def run_pinned_env(self):
        return {
            "AGTERMCTL": "agtermctl-under-test",
            "PEER_CHAT_SOCKET": "/fake/agterm.sock",
        }

    def test_socket_replacement_blocks_mid_body(self):
        world = CtlWorld(self.info())
        world.armed_swap = True  # the server restarts after the first event
        with self.assertRaises(pc.PromptBlocked) as ctx:
            self.run_pinned(world, long_message(600))
        self.assertEqual(ctx.exception.reason, "socket_replaced")
        self.assertEqual(len(world.type_events), 1)
        self.assertEqual(world.submit_count, 0)

    def test_pane_id_mismatch_refuses_before_any_event(self):
        world = CtlWorld(self.info())
        profile = world.pinned_profile()
        profile.pane_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        env = self.run_pinned_env()
        with patch.dict(pc.__dict__, {
            "ctl": world.ctl,
            "socket_fingerprint": world.fake_fingerprint,
            "time": FakeTime(),
        }), patch.dict(os.environ, env), self.assertRaises(pc.PromptBlocked) as ctx:
            pc.send_with_retry("sid", profile, "ping", window="win")
        self.assertEqual(ctx.exception.reason, "pane_replaced")
        self.assertEqual(world.type_events, [])
        self.assertEqual(world.submit_count, 0)

    def test_vanished_session_refuses_before_any_event(self):
        world = CtlWorld(self.info())
        profile = world.pinned_profile()

        def no_session(sid, window=None):
            raise RuntimeError("no such session: sid")

        env = self.run_pinned_env()
        with patch.dict(pc.__dict__, {
            "ctl": world.ctl,
            "socket_fingerprint": world.fake_fingerprint,
            "find_node": no_session,
            "time": FakeTime(),
        }), patch.dict(os.environ, env), self.assertRaises(RuntimeError) as ctx:
            pc.send_with_retry("sid", profile, "ping", window="win")
        self.assertIn("nothing was typed", str(ctx.exception))
        self.assertEqual(world.type_events, [])
        self.assertEqual(world.submit_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
