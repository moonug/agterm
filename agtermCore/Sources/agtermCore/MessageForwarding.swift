import Foundation

/// MessageForward rebuilds a `msg.*` request as the far side's `agtermctl msg` arguments, so a cross-host
/// forward runs the same command on the instance that owns the addresses.
public enum MessageForward {
    public static func arguments(for request: ControlRequest) -> (words: [String], operands: [String])? {
        let args = request.args ?? ControlArgs()
        switch request.cmd {
        case .msgSend:
            guard let body = args.body else { return nil }
            var words = ["msg", "send"]
            words += (args.recipients ?? []).flatMap { ["--to", $0] }
            if let workspace = args.workspace { words += ["--workspace", workspace] }
            if let identity = args.identity {
                words += ["--as", identity]
            } else if let from = args.from {
                words += ["--from", from]
            }
            return (words, [body])
        case .msgRead:
            guard let identity = args.identity else { return nil }
            var words = ["msg", "read", "--as", identity]
            if args.ack == true { words.append("--ack") }
            return (words, [])
        case .msgAck:
            guard let identity = args.identity, let ids = args.ids, !ids.isEmpty else { return nil }
            return (["msg", "ack", "--as", identity], ids)
        default:
            return nil
        }
    }

    /// hasNewlineOperand reports text whose newline the far login shell splits into separate commands
    /// under tcsh (docs/backlog/mail-forward-body-newline-executes-on-recipient-host.md).
    /// shortcut: refuses newline operands cross-host until the transport is shell-neutral; drop it when it is.
    public static func hasNewlineOperand(words: [String], operands: [String]) -> Bool {
        (words + operands).contains { $0.rangeOfCharacter(from: .newlines) != nil }
    }

    /// noAnswerNote is what a lost answer may have left behind, nil for commands that are safe to retry:
    /// a send may have delivered and a read --ack may have consumed the batch, while a plain read removes
    /// nothing and an explicit ack by id is idempotent.
    public static func noAnswerNote(for request: ControlRequest) -> String? {
        switch request.cmd {
        case .msgSend: return "the message may have arrived, do not resend it"
        case .msgRead where request.args?.ack == true: return "the batch may have been consumed, do not read again"
        default: return nil
        }
    }

    /// markRemote stamps a forwarded reply so a `--wait` reader sees the mailbox lives on another machine.
    public static func markRemote(_ reply: ControlResponse) -> ControlResponse {
        guard reply.ok else { return reply }
        var result = reply.result ?? ControlResult()
        result.mailRemote = true
        return ControlResponse(ok: true, result: result)
    }
}
