#!/usr/bin/env python3
"""name the stripped zig-internal libghostty frames of a `sample` or crash report.

release builds keep the exported ghostty_* symbols, which atos resolves, and strip the zig-internal
functions, which the linker also reorders. this matches the instructions before each return address
in the shipped binary against the disassembly of libghostty-internal.a, immediates masked, and names
the enclosing function when exactly one place matches.

usage: symbolicate-frames.py BINARY ARCHIVE OFFSET [OFFSET...]
       symbolicate-frames.py --test

BINARY is the executable or dylib the report came from, ARCHIVE the libghostty-internal.a of the
same ghostty revision, and each OFFSET a frame's hex offset from the image's load address.
exit status is 1 when any offset stays unresolved.
"""

import argparse
import re
import subprocess
import sys
import unittest

INSTRUCTION = re.compile(r"^\s*([0-9a-f]+):\s+(\S+)\s*(.*)$")
FUNCTION = re.compile(r"^([0-9a-f]+) <(.+)>:$")
TEXT_VMADDR = re.compile(r"segname __TEXT\s+vmaddr (0x[0-9a-f]+)")


def normalize(mnemonic: str, operands: str) -> str:
    """keep the mnemonic and registers; addresses and immediates differ between the two links."""
    operands = re.sub(r"<[^>]*>", "", operands)
    operands = re.sub(r"#?-?0x[0-9a-f]+|#-?\d+|\b\d+\b", "N", operands)
    return mnemonic + " " + re.sub(r"\s+", "", operands)


def disassemble(path: str) -> list[str]:
    cmd = ["xcrun", "llvm-objdump", "-d", "--no-show-raw-insn", path]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.splitlines()


def text_base(path: str) -> int:
    out = subprocess.run(["xcrun", "otool", "-l", path], check=True, capture_output=True, text=True).stdout
    found = TEXT_VMADDR.search(out)
    if not found:
        raise ValueError(f"{path}: no __TEXT segment")
    return int(found.group(1), 16)


def window_sizes(value: str) -> list[int]:
    """argparse type for --windows: positive sizes, largest first, as `resolve` relies on."""
    try:
        sizes = [int(size) for size in value.split(",")]
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"not a list of integers: {value}") from err
    if not sizes or min(sizes) <= 0 or sizes != sorted(set(sizes), reverse=True):
        raise argparse.ArgumentTypeError("sizes must be positive and strictly descending")
    return sizes


class Disassembly:
    """instructions in file order, each with its address and the function it sits in."""

    def __init__(self, lines: list[str]) -> None:
        self.addresses: list[int] = []
        self.tokens: list[str] = []
        self.functions: list[tuple[int, str] | None] = []
        function: tuple[int, str] | None = None
        for line in lines:
            header = FUNCTION.match(line)
            if header:
                function = (int(header.group(1), 16), header.group(2))
                continue
            found = INSTRUCTION.match(line)
            if not found:
                continue
            self.addresses.append(int(found.group(1), 16))
            self.tokens.append(normalize(found.group(2), found.group(3)))
            self.functions.append(function)

    def index_of(self, address: int) -> int | None:
        """position of the instruction at `address`, none when it is not an instruction boundary."""
        try:
            return self.addresses.index(address)
        except ValueError:
            return None

    def window_before(self, end: int, size: int) -> list[str] | None:
        """the `size` instructions ending just before position `end`, none when fewer precede it."""
        return self.tokens[end - size:end] if end >= size else None

    def matches(self, window: list[str]) -> list[int]:
        """position of the last instruction of every place `window` occurs."""
        size, last = len(window), window[-1]
        return [i for i, token in enumerate(self.tokens)
                if token == last and i + 1 >= size and self.tokens[i + 1 - size:i + 1] == window]

    def describe(self, index: int) -> str:
        function = self.functions[index]
        if function is None:
            return f"0x{self.addresses[index]:x}"
        return f"{function[1]}+0x{self.addresses[index] - function[0]:x}"


def resolve(binary: Disassembly, archive: Disassembly, address: int, sizes: list[int]) -> tuple[bool, str]:
    """whether `address` named exactly one place in the archive, and the line to print for it."""
    end = binary.index_of(address)
    if end is None:
        return False, "not an instruction boundary in the binary"
    for size in sizes:
        window = binary.window_before(end, size)
        if window is None:
            continue
        hits = archive.matches(window)
        if len(hits) == 1:
            return True, f"{archive.describe(hits[0])} (window {size})"
        # sizes descend and a smaller window can only match more places, so stop here
        if len(hits) > 1:
            places = ", ".join(archive.describe(hit) for hit in hits[:5])
            return False, f"ambiguous, {len(hits)} matches at window {size}: {places}"
    return False, "no match"


class Tests(unittest.TestCase):
    ARCHIVE = [
        "0000000000000000 <alpha>:",
        "       0: mov x0, x1",
        "       4: add x0, x0, #0x10",
        "       8: bl 0x100 <beta>",
        "       c: ret",
        "0000000000000010 <beta>:",
        "      10: mov x0, x1",
        "      14: sub x0, x0, #0x20",
        "      18: bl 0x200 <gamma>",
        "      1c: ret",
    ]
    BINARY = [
        "100001000: mov x0, x1",
        "100001004: add x0, x0, #0x48",
        "100001008: bl 0x100009000",
        "10000100c: ret",
    ]

    def setUp(self) -> None:
        self.archive = Disassembly(self.ARCHIVE)
        self.binary = Disassembly(self.BINARY)

    def test_normalize_masks_addresses_and_immediates(self) -> None:
        self.assertEqual(normalize("add", "x0, x0, #0x10"), normalize("add", "x0,  x0, #72"))
        self.assertEqual(normalize("bl", "0x100 <beta>"), "bl N")
        self.assertNotEqual(normalize("add", "x0, x0, #1"), normalize("add", "x1, x0, #1"))

    def test_unique_match_names_the_function(self) -> None:
        self.assertEqual(resolve(self.binary, self.archive, 0x10000100c, [3]), (True, "alpha+0x8 (window 3)"))

    def test_ambiguous_match_is_not_named(self) -> None:
        resolved, text = resolve(self.binary, self.archive, 0x100001004, [1])
        self.assertFalse(resolved)
        self.assertTrue(text.startswith("ambiguous, 2 matches at window 1: alpha+0x0, beta+0x0"), text)

    def test_no_match(self) -> None:
        binary = Disassembly(["100001000: nop", "100001004: nop", "100001008: ret"])
        self.assertEqual(resolve(binary, self.archive, 0x100001008, [2]), (False, "no match"))

    def test_a_window_longer_than_the_history_falls_through_to_a_smaller_one(self) -> None:
        self.assertEqual(resolve(self.binary, self.archive, 0x10000100c, [24, 3]), (True, "alpha+0x8 (window 3)"))

    def test_an_address_between_instructions_is_reported_as_such(self) -> None:
        self.assertEqual(resolve(self.binary, self.archive, 0x100001006, [3]),
                         (False, "not an instruction boundary in the binary"))

    def test_window_sizes_must_descend(self) -> None:
        self.assertEqual(window_sizes("24,12"), [24, 12])
        for bad in ("3,24", "12,12", "0", "a", ""):
            with self.assertRaises(argparse.ArgumentTypeError, msg=bad):
                window_sizes(bad)


def main() -> int:
    if "--test" in sys.argv[1:]:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Tests)
        return 0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("binary")
    parser.add_argument("archive")
    parser.add_argument("offsets", nargs="+", metavar="offset")
    parser.add_argument("--windows", type=window_sizes, default=[24, 12],
                        help="instruction window sizes to try, largest first (default: 24,12)")
    args = parser.parse_args()

    try:
        base = text_base(args.binary)
        binary = Disassembly(disassemble(args.binary))
        archive = Disassembly(disassemble(args.archive))
        addresses = [base + int(offset, 16) for offset in args.offsets]
    except (subprocess.CalledProcessError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    unresolved = 0
    for offset, address in zip(args.offsets, addresses):
        resolved, text = resolve(binary, archive, address, args.windows)
        unresolved += not resolved
        print(f"{offset}: {text}")
    return 1 if unresolved else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
