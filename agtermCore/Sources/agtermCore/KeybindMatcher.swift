import Foundation

/// The outcome of feeding one chord to a `KeybindMatcher`.
public enum MatchResult: Equatable, Sendable {
    /// The pending prefix plus this chord exactly matches a bound keybind; the matcher has reset, keeping only
    /// a repeatable sequence's repeat window.
    case fired(KeybindTarget)
    /// The pending prefix plus this chord is a strict prefix of a longer bind; the matcher now awaits the
    /// next chord (the leader is armed).
    case armed
    /// No bind starts with the pending prefix plus this chord; the matcher has reset.
    case unmatched
}

/// The leader/sequence state machine that turns a stream of chords into command fires.
///
/// Built from `(Keybind, KeybindTarget)` pairs, it holds the chords typed so far as a pending prefix;
/// `advance(_:)` consumes one chord and reports `.fired`/`.armed`/`.unmatched`. Deadline-free — the leader
/// timeout that abandons a half-typed sequence is app-side and calls `reset()`, as Esc does. No AppKit, no
/// timers.
public struct KeybindMatcher: Sendable {
    private let binds: [(keybind: Keybind, target: KeybindTarget)]
    private let repeating: Set<KeybindTarget>
    private var pending: [Chord] = []
    /// The prefix a repeatable sequence just fired under (tmux `bind -r`): until `reset()`, the last chord of
    /// any repeatable bind sharing it fires again without retyping the prefix.
    private var repeatPrefix: [Chord]?

    /// `repeating` names the targets whose leader sequences stay live for another press after firing.
    public init(_ binds: [(Keybind, KeybindTarget)], repeating: Set<KeybindTarget> = []) {
        self.binds = binds.map { (keybind: $0.0, target: $0.1) }
        self.repeating = repeating
    }

    /// Whether a sequence is partway through (a leader is armed). The app uses this to gate the timeout
    /// timer and the on-screen hint.
    public var isArmed: Bool { !pending.isEmpty }

    /// Whether a repeatable sequence just fired and its prefix is still live. Not `isArmed`: a key that
    /// repeats nothing ends the window and is matched afresh, so Esc and ordinary typing still pass through.
    public var isRepeating: Bool { repeatPrefix != nil }

    /// Feed one chord: an exact match fires and resets, a strict prefix arms (keeping the pending prefix for
    /// the next chord), anything else is unmatched and resets. When armed, a chord completing no bind resets
    /// so the caller can pass it through to the terminal — UNLESS the chord is itself a fresh leader, in
    /// which case the matcher re-arms on it, so re-pressing a leader restarts rather than abandons the
    /// sequence.
    public mutating func advance(_ chord: Chord) -> MatchResult {
        if let prefix = repeatPrefix {
            repeatPrefix = nil
            if let bind = repeatBind(prefix: prefix, tail: chord) {
                repeatPrefix = prefix
                return .fired(bind.target)
            }
        }

        let candidate = pending + [chord]

        for bind in binds where bind.keybind == candidate {
            pending = []
            if candidate.count > 1, repeating.contains(bind.target) { repeatPrefix = Array(candidate.dropLast()) }
            return .fired(bind.target)
        }

        if binds.contains(where: { isStrictKeybindPrefix(candidate, of: $0.keybind) }) {
            pending = candidate
            return .armed
        }

        // the extended prefix matches nothing; if the chord on its own is a fresh leader (or an
        // exact single-chord bind), restart from it instead of dropping the press.
        if isArmed {
            pending = []
            return advance(chord)
        }

        pending = []
        return .unmatched
    }

    /// Clear the pending prefix and any repeat window (Esc or the app-side timeout).
    public mutating func reset() {
        pending = []
        repeatPrefix = nil
    }

    /// Whether `chord` would fire again inside the open repeat window. Lets the app route autorepeat of a held
    /// tail to `advance` while every other consumed key's autorepeat stays swallowed.
    public func isRepeatTail(_ chord: Chord) -> Bool {
        repeatPrefix.map { repeatBind(prefix: $0, tail: chord) != nil } ?? false
    }

    private func repeatBind(prefix: [Chord], tail: Chord) -> (keybind: Keybind, target: KeybindTarget)? {
        binds.first { repeating.contains($0.target) && $0.keybind == prefix + [tail] }
    }
}
