import Foundation
import Testing
@testable import agtermCore

struct KeybindMatcherTests {
    private let ctrlA = Chord(mods: .control, key: "a")
    private let b = Chord(mods: [], key: "b")
    private let c = Chord(mods: [], key: "c")
    private let cmdShiftU = Chord(mods: [.command, .shift], key: "u")

    @Test func simpleChordFires() {
        let target = KeybindTarget.command(UUID())
        var matcher = KeybindMatcher([([cmdShiftU], target)])
        #expect(matcher.advance(cmdShiftU) == .fired(target))
        #expect(!matcher.isArmed)
    }

    @Test func unmatchedSingleChord() {
        let target = KeybindTarget.command(UUID())
        var matcher = KeybindMatcher([([cmdShiftU], target)])
        #expect(matcher.advance(ctrlA) == .unmatched)
        #expect(!matcher.isArmed)
    }

    @Test func sequenceFiresOnSecondChord() {
        let target = KeybindTarget.command(UUID())
        var matcher = KeybindMatcher([([ctrlA, b], target)])
        #expect(matcher.advance(ctrlA) == .armed)
        #expect(matcher.isArmed)
        #expect(matcher.advance(b) == .fired(target))
        #expect(!matcher.isArmed)
    }

    @Test func wrongSecondChordResetsAndUnmatches() {
        let target = KeybindTarget.command(UUID())
        var matcher = KeybindMatcher([([ctrlA, b], target)])
        #expect(matcher.advance(ctrlA) == .armed)
        #expect(matcher.advance(c) == .unmatched)
        #expect(!matcher.isArmed)
        #expect(matcher.advance(ctrlA) == .armed)
    }

    @Test func resetClearsPending() {
        let target = KeybindTarget.command(UUID())
        var matcher = KeybindMatcher([([ctrlA, b], target)])
        #expect(matcher.advance(ctrlA) == .armed)
        matcher.reset()
        #expect(!matcher.isArmed)
        #expect(matcher.advance(b) == .unmatched)
    }

    @Test func twoSequencesSharingLeader() {
        let targetB = KeybindTarget.command(UUID())
        let targetC = KeybindTarget.command(UUID())
        var matcher = KeybindMatcher([([ctrlA, b], targetB), ([ctrlA, c], targetC)])
        #expect(matcher.advance(ctrlA) == .armed)
        #expect(matcher.advance(b) == .fired(targetB))

        #expect(matcher.advance(ctrlA) == .armed)
        #expect(matcher.advance(c) == .fired(targetC))
    }

    @Test func simpleAndSequenceCoexist() {
        let simpleTarget = KeybindTarget.command(UUID())
        let sequenceTarget = KeybindTarget.command(UUID())
        var matcher = KeybindMatcher([([cmdShiftU], simpleTarget), ([ctrlA, b], sequenceTarget)])
        #expect(matcher.advance(cmdShiftU) == .fired(simpleTarget))
        #expect(matcher.advance(ctrlA) == .armed)
        #expect(matcher.advance(b) == .fired(sequenceTarget))
    }

    @Test func rePressingLeaderWhileArmedReArms() {
        let target = KeybindTarget.command(UUID())
        var matcher = KeybindMatcher([([ctrlA, b], target)])
        #expect(matcher.advance(ctrlA) == .armed)
        #expect(matcher.advance(ctrlA) == .armed)
        #expect(matcher.isArmed)
        #expect(matcher.advance(b) == .fired(target))
    }

    @Test func wrongChordWhileArmedThatIsItselfASimpleBindFires() {
        let sequenceTarget = KeybindTarget.command(UUID())
        let simpleTarget = KeybindTarget.command(UUID())
        var matcher = KeybindMatcher([([ctrlA, b], sequenceTarget), ([cmdShiftU], simpleTarget)])
        #expect(matcher.advance(ctrlA) == .armed)
        #expect(matcher.advance(cmdShiftU) == .fired(simpleTarget))
        #expect(!matcher.isArmed)
    }

    @Test func alternativesShareOneTarget() {
        let target = KeybindTarget.command(UUID())
        var matcher = KeybindMatcher([([cmdShiftU], target), ([ctrlA, b], target)])
        #expect(matcher.advance(cmdShiftU) == .fired(target))
        #expect(matcher.advance(ctrlA) == .armed)
        #expect(matcher.advance(b) == .fired(target))
    }

    @Test func builtinAndCommandTargetsCoexist() {
        let command = KeybindTarget.command(UUID())
        let builtin = KeybindTarget.builtin(.toggleSplit)
        var matcher = KeybindMatcher([([cmdShiftU], command), ([ctrlA, b], builtin)])
        #expect(matcher.advance(cmdShiftU) == .fired(command))
        #expect(matcher.advance(ctrlA) == .armed)
        #expect(matcher.advance(b) == .fired(builtin))
    }

    @Test func emptyMatcherUnmatches() {
        var matcher = KeybindMatcher([])
        #expect(matcher.advance(ctrlA) == .unmatched)
        #expect(!matcher.isArmed)
    }

    @Test func repeatingSequenceFiresAgainOnItsLastChordAlone() {
        let target = KeybindTarget.builtin(.nextSession)
        var matcher = KeybindMatcher([([ctrlA, b], target)], repeating: [target])
        #expect(matcher.advance(ctrlA) == .armed)
        #expect(!matcher.isRepeatTail(b), "armed, not repeating")
        #expect(matcher.advance(b) == .fired(target))
        #expect(matcher.isRepeating)
        #expect(!matcher.isArmed)
        #expect(matcher.isRepeatTail(b))
        #expect(!matcher.isRepeatTail(ctrlA))
        #expect(matcher.advance(b) == .fired(target))
        #expect(matcher.advance(b) == .fired(target))
    }

    @Test func repeatWindowSwitchesBetweenRepeatingSiblingsOnly() {
        let next = KeybindTarget.builtin(.nextSession)
        let previous = KeybindTarget.builtin(.previousSession)
        let once = KeybindTarget.command(UUID())
        let d = Chord(mods: [], key: "d")
        var matcher = KeybindMatcher([([ctrlA, b], next), ([ctrlA, c], previous), ([ctrlA, d], once)],
                                     repeating: [next, previous])
        _ = matcher.advance(ctrlA)
        #expect(matcher.advance(b) == .fired(next))
        #expect(matcher.advance(c) == .fired(previous))
        #expect(matcher.advance(d) == .unmatched)
        #expect(!matcher.isRepeating)
    }

    @Test func keyOutsideTheRepeatWindowIsMatchedAfresh() {
        let next = KeybindTarget.builtin(.nextSession)
        let simple = KeybindTarget.command(UUID())
        var matcher = KeybindMatcher([([ctrlA, b], next), ([cmdShiftU], simple)], repeating: [next])
        _ = matcher.advance(ctrlA)
        _ = matcher.advance(b)
        #expect(matcher.advance(cmdShiftU) == .fired(simple))
        #expect(matcher.advance(b) == .unmatched)

        _ = matcher.advance(ctrlA)
        _ = matcher.advance(b)
        #expect(matcher.advance(ctrlA) == .armed, "a leader inside the window starts a new sequence")
    }

    @Test func resetClosesTheRepeatWindow() {
        let target = KeybindTarget.builtin(.nextSession)
        var matcher = KeybindMatcher([([ctrlA, b], target)], repeating: [target])
        _ = matcher.advance(ctrlA)
        _ = matcher.advance(b)
        matcher.reset()
        #expect(matcher.advance(b) == .unmatched)
    }

    @Test func singleChordAndNonRepeatingSequenceOpenNoWindow() {
        let simple = KeybindTarget.builtin(.toggleSplit)
        let sequence = KeybindTarget.builtin(.nextSession)
        var matcher = KeybindMatcher([([cmdShiftU], simple), ([ctrlA, b], sequence)], repeating: [simple])
        #expect(matcher.advance(cmdShiftU) == .fired(simple))
        #expect(!matcher.isRepeating)
        _ = matcher.advance(ctrlA)
        #expect(matcher.advance(b) == .fired(sequence))
        #expect(!matcher.isRepeating)
    }
}
