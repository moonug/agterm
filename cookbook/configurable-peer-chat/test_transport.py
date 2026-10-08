#!/usr/bin/env python3
"""In-memory transport tests for peer-chat.py.

The terminal surface (composer_state, type_text, pane_text, ctl, clock) is
replaced by a linear composer model rendered as an opencode-style box, so the
real clear_composer / type_body / send / send_with_retry run without touching
any live agterm pane. Any attempt to reach the real CLI fails the test.
"""

import importlib.util
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
        with Harness(pane):
            with self.assertRaises(pc.ComposerDirty) as ctx:
                pc.send_with_retry("sid", opencode_profile(), message)
        err = str(ctx.exception)
        self.assertIn("composer cleared", err)
        self.assertNotIn("cleanup failed", err)
        self.assertNotIn("КВИНТЭССЕНЦИЯ", err)
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
        with Harness(pane):
            with self.assertRaises(pc.ComposerDirty) as ctx:
                pc.send_with_retry("sid", opencode_profile(), message)
        err = str(ctx.exception)
        self.assertIn("cleanup failed", err)
        self.assertIn("blind deletion refused", err)
        self.assertNotIn("КВИНТЭССЕНЦИЯ", err)
        self.assertEqual(pane.submit_count, 0)
        # Nothing is deleted: the composer still holds the owned prefix and
        # the foreign text, so the user can read what happened in the pane.
        self.assertIn(foreign, pane.text)
        self.assertIn("Тест доставки длинного сообщения", pane.text)

    def test_ambiguous_submit_is_not_retried(self):
        message = long_message(800)
        pane = FakePane(accept_submit=False)
        with Harness(pane):
            with self.assertRaises(pc.DeliveryAmbiguous) as ctx:
                pc.send_with_retry("sid", opencode_profile(), message)
        self.assertIn("do not resend", str(ctx.exception))
        self.assertEqual(pane.submit_count, 1)

    def test_diag_reports_sizes_without_message_text(self):
        message = long_message(4000)
        pane = FakePane(cap=3900)
        with Harness(pane):
            with self.assertRaises(pc.ComposerDirty) as ctx:
                pc.send_with_retry("sid", opencode_profile(), message)
        err = str(ctx.exception)
        self.assertRegex(err, r"chunk \d+/\d+ was typed")
        self.assertIn("expected ~", err)
        self.assertIn("chars in 6 rows", err)
        self.assertNotIn("КВИНТЭССЕНЦИЯ", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
