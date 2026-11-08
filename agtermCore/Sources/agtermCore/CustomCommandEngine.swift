import Foundation

/// Host-free monitor matcher: indexes parsed commands by id, builds a keybind matcher over every alternative
/// of every keyed command plus the built-in binds the menu cannot carry, and resolves each chord to what
/// should run. A shortcut's alternatives become separate matcher entries sharing one target, so any of them
/// fires the same command.
public struct CustomCommandEngine: Sendable {
    private var matcher: KeybindMatcher
    private let commandsByID: [UUID: CustomCommand]

    /// `builtinSequences` and `builtinRepeating` are `Keymap`'s, which owns what belongs in them.
    public init(commands: [CustomCommand], builtinSequences: [BuiltinAction: [Keybind]] = [:],
                builtinRepeating: Set<BuiltinAction> = []) {
        commandsByID = Dictionary(commands.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })

        var binds: [(Keybind, KeybindTarget)] = []
        var repeating = Set(builtinRepeating.map(KeybindTarget.builtin))
        for command in commands where !command.shortcut.isEmpty {
            guard let keybinds = parseKeybinds(command.shortcut) else { continue }
            binds += keybinds.map { ($0, .command(command.id)) }
            if command.repeats { repeating.insert(.command(command.id)) }
        }
        // sorted so registration order does not vary with dictionary hashing.
        for (action, keybinds) in builtinSequences.sorted(by: { $0.key.rawValue < $1.key.rawValue }) {
            binds += keybinds.map { ($0, .builtin(action)) }
        }
        matcher = KeybindMatcher(binds, repeating: repeating)
    }

    public enum Outcome: Equatable, Sendable {
        case fired(CustomCommand)
        case firedBuiltin(BuiltinAction)
        case armed
        case unmatched
    }

    public mutating func advance(_ chord: Chord) -> Outcome {
        switch matcher.advance(chord) {
        case .fired(.command(let id)):
            commandsByID[id].map(Outcome.fired) ?? .unmatched
        case .fired(.builtin(let action)):
            .firedBuiltin(action)
        case .armed:
            .armed
        case .unmatched:
            .unmatched
        }
    }

    public var isArmed: Bool { matcher.isArmed }

    public var isRepeating: Bool { matcher.isRepeating }

    public func isRepeatTail(_ chord: Chord) -> Bool { matcher.isRepeatTail(chord) }

    public mutating func reset() {
        matcher.reset()
    }
}
