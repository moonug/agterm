import Foundation
import Testing
@testable import agtermCore

struct CustomCommandEngineTests {
    private let ctrlA = Chord(mods: .control, key: "a")
    private let g = Chord(mods: [], key: "g")
    private let x = Chord(mods: [], key: "x")
    private let cmdShiftE = Chord(mods: [.command, .shift], key: "e")

    @Test func firesSimpleCommand() {
        let command = CustomCommand(name: "edit", command: "zed {AGT_SESSION_PWD}", shortcut: "cmd+shift+e")
        var engine = CustomCommandEngine(commands: [command])

        #expect(engine.advance(cmdShiftE) == .fired(command))
        #expect(!engine.isArmed)
    }

    @Test func armsThenFiresLeaderCommand() {
        let command = CustomCommand(name: "git", command: "git status", shortcut: "ctrl+a>g")
        var engine = CustomCommandEngine(commands: [command])

        #expect(engine.advance(ctrlA) == .armed)
        #expect(engine.isArmed)
        #expect(engine.advance(g) == .fired(command))
        #expect(!engine.isArmed)
    }

    @Test func unmatchedChordPassesThrough() {
        let command = CustomCommand(name: "edit", command: "zed", shortcut: "cmd+shift+e")
        var engine = CustomCommandEngine(commands: [command])

        #expect(engine.advance(ctrlA) == .unmatched)
        #expect(!engine.isArmed)
    }

    @Test func resetAbandonsLeaderSequence() {
        let command = CustomCommand(name: "git", command: "git status", shortcut: "ctrl+a>g")
        var engine = CustomCommandEngine(commands: [command])

        #expect(engine.advance(ctrlA) == .armed)
        engine.reset()
        #expect(!engine.isArmed)
        #expect(engine.advance(g) == .unmatched)
    }

    @Test func paletteOnlyCommandNeverMatchesAChord() {
        let command = CustomCommand(name: "palette", command: "touch /tmp/x", shortcut: "")
        var engine = CustomCommandEngine(commands: [command])

        #expect(engine.advance(cmdShiftE) == .unmatched)
        #expect(engine.advance(ctrlA) == .unmatched)
    }

    @Test func invalidShortcutCommandNeverMatchesAChord() {
        let command = CustomCommand(name: "bad", command: "echo bad", shortcut: "ctrl+missing")
        var engine = CustomCommandEngine(commands: [command])

        #expect(engine.advance(ctrlA) == .unmatched)
    }

    @Test func wrongLeaderContinuationCanRestartAsFreshLeader() {
        let command = CustomCommand(name: "git", command: "git status", shortcut: "ctrl+a>g")
        var engine = CustomCommandEngine(commands: [command])

        #expect(engine.advance(ctrlA) == .armed)
        #expect(engine.advance(ctrlA) == .armed)
        #expect(engine.advance(g) == .fired(command))
    }

    @Test func wrongLeaderContinuationCanFireSimpleCommand() {
        let sequence = CustomCommand(name: "git", command: "git status", shortcut: "ctrl+a>g")
        let simple = CustomCommand(name: "edit", command: "zed", shortcut: "cmd+shift+e")
        var engine = CustomCommandEngine(commands: [sequence, simple])

        #expect(engine.advance(ctrlA) == .armed)
        #expect(engine.advance(cmdShiftE) == .fired(simple))
        #expect(!engine.isArmed)
    }

    @Test func emptyEngineUnmatches() {
        var engine = CustomCommandEngine(commands: [])

        #expect(engine.advance(x) == .unmatched)
        #expect(!engine.isArmed)
    }

    @Test func eitherAlternativeFiresTheSameCommand() {
        let command = CustomCommand(name: "mc", command: "mc", shortcut: "cmd+shift+e|ctrl+a>g")
        var engine = CustomCommandEngine(commands: [command])

        #expect(engine.advance(cmdShiftE) == .fired(command))
        #expect(engine.advance(ctrlA) == .armed)
        #expect(engine.advance(g) == .fired(command))
    }

    @Test func malformedAlternativePoisonsTheWholeShortcut() {
        let command = CustomCommand(name: "bad", command: "echo bad", shortcut: "cmd+shift+e|esc")
        var engine = CustomCommandEngine(commands: [command])

        #expect(engine.advance(cmdShiftE) == .unmatched)
    }

    @Test func builtinSequenceFiresItsAction() {
        var engine = CustomCommandEngine(commands: [], builtinSequences: [.toggleSplit: [[ctrlA, g]]])

        #expect(engine.advance(ctrlA) == .armed)
        #expect(engine.advance(g) == .firedBuiltin(.toggleSplit))
        #expect(!engine.isArmed)
    }

    @Test func builtinAlternativesAndCommandsShareOneMatcher() {
        let command = CustomCommand(name: "edit", command: "zed", shortcut: "cmd+shift+e")
        var engine = CustomCommandEngine(commands: [command],
                                         builtinSequences: [.toggleSplit: [[ctrlA, g], [ctrlA, x]]])

        #expect(engine.advance(cmdShiftE) == .fired(command))
        #expect(engine.advance(ctrlA) == .armed)
        #expect(engine.advance(x) == .firedBuiltin(.toggleSplit))
    }

    @Test func repeatFlagFromEitherVerbKeepsItsSequenceLive() throws {
        let parsed = parseKeymap("""
            map ctrl+a>g --repeat next_session
            command "Grow" ctrl+a>x --repeat agtermctl session resize +0.05
            """)
        try #require(parsed.diagnostics.isEmpty)
        let keymap = parsed.keymap
        let command = try #require(keymap.commands.first)
        var engine = CustomCommandEngine(commands: keymap.commands, builtinSequences: keymap.builtinSequences,
                                         builtinRepeating: keymap.builtinRepeating)

        #expect(engine.advance(ctrlA) == .armed)
        #expect(engine.advance(g) == .firedBuiltin(.nextSession))
        #expect(engine.advance(g) == .firedBuiltin(.nextSession))
        #expect(engine.advance(x) == .fired(command))
        #expect(engine.isRepeating)
    }

    @Test func sequenceWithoutTheFlagFiresOnce() throws {
        let keymap = parseKeymap("map ctrl+a>g next_session").keymap
        var engine = CustomCommandEngine(commands: [], builtinSequences: keymap.builtinSequences,
                                         builtinRepeating: keymap.builtinRepeating)

        _ = engine.advance(ctrlA)
        #expect(engine.advance(g) == .firedBuiltin(.nextSession))
        #expect(!engine.isRepeating)
        #expect(engine.advance(g) == .unmatched)
    }
}
