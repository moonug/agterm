import Foundation

/// MessagePaneError is a bus address the host could not resolve to a live pane, or a host without the bus.
/// Only `unresolved` becomes a per-recipient rejection; `unsupported` refuses the whole command.
public struct MessagePaneError: Error, Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case unresolved
        case unsupported
        /// remote names an address owned by a remote window's server; `detail` is that window's id.
        case remote
    }

    public static let unsupported = MessagePaneError(ControlActionsUnsupported.message("msg"), kind: .unsupported)

    public let message: String
    public let kind: Kind
    /// detail becomes an `.unresolved` recipient's `MessageRejection.detail`.
    public let detail: String?

    public init(_ message: String, kind: Kind = .unresolved, detail: String? = nil) {
        self.message = message
        self.kind = kind
        self.detail = detail
    }
}

extension ControlDispatcher {
    /// dispatchMessageCommand parses and validates `msg.*`, asks the host only to resolve addresses and
    /// report a queued message, and runs the mailbox operation itself.
    func dispatchMessageCommand(_ request: ControlRequest) async -> ControlResponse {
        let args = request.args ?? ControlArgs()
        switch request.cmd {
        case .msgSend: return await dispatchMessageSend(args)
        case .msgRead: return await dispatchMessageRead(request)
        case .msgAck: return await dispatchMessageAck(request)
        default: preconditionFailure("unexpected msg command: \(request.cmd.rawValue)")
        }
    }

    private func dispatchMessageSend(_ args: ControlArgs) async -> ControlResponse {
        guard let body = args.body else { return failure("msg.send requires a body") }
        let addresses = args.recipients ?? []
        let all = args.all == true
        if all, !addresses.isEmpty { return failure("msg.send takes --to or --all, not both") }
        guard all || !addresses.isEmpty else { return failure("msg.send requires --to or --all") }
        if !all, args.workspace != nil { return failure("msg.send: --workspace narrows --all only") }
        guard body.utf8.count <= MessageBus.maxBodyBytes else {
            return failure("message body too large (\(body.utf8.count) bytes, max \(MessageBus.maxBodyBytes))")
        }
        var sender: MessageRecipient?
        var senderRemote = false
        if let identity = args.identity {
            switch resolveIdentity(identity) {
            case .failure(let error) where error.kind == .remote: senderRemote = true
            case .failure(let error): return failure(error.message)
            case .success(let resolved): sender = resolved
            }
        } else if let from = args.from {
            let address: MessageAddress
            do { address = try MessageAddress.identity(from) } catch { return failure(error.message) }
            guard case .id(let pane) = address.pane else { return failure("--from must name a pane id") }
            sender = MessageRecipient(pane: pane, address: from)
        }

        var targets: [MessageRecipient] = []
        var rejected: [MessageRejection] = []
        var remoteAddresses: [String] = []
        var remoteWindow: UUID?
        var remoteWindowsDiffer = false
        if all {
            if senderRemote { return failure("msg.send --all cannot act as a remote pane") }
            switch actions.messagePanes(workspace: args.workspace, window: args.window) {
            case .failure(let error): return failure(error.message)
            case .success(let panes): targets = panes.filter { $0.pane != sender?.pane }
            }
        } else {
            for text in addresses {
                let address: MessageAddress
                do { address = try MessageAddress.recipient(text) } catch { return failure(error.message) }
                switch actions.resolveMessagePane(address) {
                case .success(let recipient): targets.append(recipient)
                case .failure(let error) where error.kind == .unsupported: return failure(error.message)
                case .failure(let error) where error.kind == .remote:
                    remoteAddresses.append(text)
                    let window = error.detail.flatMap { UUID(uuidString: $0) }
                    if remoteWindow == nil { remoteWindow = window } else if remoteWindow != window { remoteWindowsDiffer = true }
                case .failure(let error): rejected.append(MessageRejection(address: text, reason: .unknownPane, detail: error.detail))
                }
            }
        }
        if !remoteAddresses.isEmpty {
            if !targets.isEmpty { return failure("msg.send cannot mix local and remote recipients") }
            if remoteWindowsDiffer { return failure("msg.send recipients live in different remote windows") }
            guard let window = remoteWindow else { return failure("remote recipient window is unknown") }
            var forward = args
            forward.recipients = remoteAddresses
            if let sender { forward.identity = nil; forward.from = sender.address }
            return await actions.messageForward(window: window, request: ControlRequest(cmd: .msgSend, args: forward))
        }
        if senderRemote { return failure("msg.send cannot act as a remote pane and message local panes") }
        var seen = Set<UUID>()
        targets = targets.filter { seen.insert($0.pane).inserted }

        let result: MessageSendResult
        do {
            result = try bus.send(body, from: sender?.address, to: targets, now: Date().timeIntervalSince1970)
        } catch {
            return failure("message body too large (\(body.utf8.count) bytes, max \(MessageBus.maxBodyBytes))")
        }
        let delivered = Set(result.delivered)
        let reached = targets.filter { delivered.contains($0.address) }
        if !reached.isEmpty { actions.messageQueued(result.messageID, from: sender?.address, to: reached) }
        return ControlResponse(ok: true, result: ControlResult(
            messageID: result.messageID.uuidString, delivered: result.delivered, rejected: rejected + result.rejected))
    }

    private func dispatchMessageRead(_ request: ControlRequest) async -> ControlResponse {
        let args = request.args ?? ControlArgs()
        let reader: MessageRecipient
        switch requireIdentity(args.identity, command: .msgRead) {
        case .failure(let error) where error.kind == .remote:
            guard let window = error.detail.flatMap({ UUID(uuidString: $0) }) else { return failure("remote pane window is unknown") }
            return await actions.messageForward(window: window, request: request)
        case .failure(let error): return failure(error.message)
        case .success(let resolved): reader = resolved
        }
        guard args.ack == true else {
            let batch = bus.read(pane: reader.pane)
            return ControlResponse(ok: true, result: ControlResult(messages: batch.messages, remaining: batch.remaining))
        }
        let batch = bus.readAndAck(pane: reader.pane)
        return ControlResponse(ok: true, result: ControlResult(
            messages: batch.messages, remaining: batch.remaining, acked: batch.messages.map(\.id.uuidString)))
    }

    private func dispatchMessageAck(_ request: ControlRequest) async -> ControlResponse {
        let args = request.args ?? ControlArgs()
        let reader: MessageRecipient
        switch requireIdentity(args.identity, command: .msgAck) {
        case .failure(let error) where error.kind == .remote:
            guard let window = error.detail.flatMap({ UUID(uuidString: $0) }) else { return failure("remote pane window is unknown") }
            return await actions.messageForward(window: window, request: request)
        case .failure(let error): return failure(error.message)
        case .success(let resolved): reader = resolved
        }
        guard let raw = args.ids, !raw.isEmpty else { return failure("msg.ack requires message ids") }
        var ids: [UUID] = []
        for text in raw {
            guard let id = UUID(uuidString: text) else { return failure("invalid message id: \(text)") }
            ids.append(id)
        }
        let removed = bus.ack(pane: reader.pane, ids: ids)
        return ControlResponse(ok: true, result: ControlResult(acked: removed.map(\.uuidString)))
    }

    private func requireIdentity(_ text: String?, command: Command) -> Result<MessageRecipient, MessagePaneError> {
        guard let text else { return .failure(MessagePaneError("\(command.rawValue) requires an identity: --as SESSION:PANE-ID")) }
        return resolveIdentity(text)
    }

    private func resolveIdentity(_ text: String) -> Result<MessageRecipient, MessagePaneError> {
        let address: MessageAddress
        do { address = try MessageAddress.identity(text) } catch { return .failure(MessagePaneError(error.message)) }
        return actions.resolveMessagePane(address)
    }

    private func failure(_ message: String) -> ControlResponse {
        ControlResponse(ok: false, error: message)
    }
}
