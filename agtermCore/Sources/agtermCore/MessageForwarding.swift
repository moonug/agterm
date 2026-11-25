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
            if let from = args.from { words += ["--from", from] }
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
}
