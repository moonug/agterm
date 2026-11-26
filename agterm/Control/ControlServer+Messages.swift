import agtermCore
import Foundation

extension ControlServer {
    private enum MessageRoute {
        case catalog(UUID, host: String)
        case attached(AttachedOrigin)

        var host: String {
            switch self {
            case .catalog(_, let host): host
            case .attached(let origin): origin.host
            }
        }

        func shares(_ other: Self) -> Bool {
            switch (self, other) {
            case (.catalog(let a, _), .catalog(let b, _)): a == b
            case (.attached(let a), .attached(let b)): a.shares(b)
            default: false
            }
        }
    }

    /// resolveMessagePane resolves the session across every open window, never the frontmost default for an
    /// id, then asks its store for the live pane.
    func resolveMessagePane(_ address: MessageAddress) -> Result<MessageRecipient, MessagePaneError> {
        let store: AppStore
        let sessionID: UUID
        switch resolver.resolveSessionTarget(address.session, window: nil) {
        case .failure(let response): return .failure(MessagePaneError(response.error ?? "no such session: \(address.session)"))
        case .success(let resolved): (store, sessionID) = resolved
        }
        // The window groups remote addresses for dispatch; an attachment still needs its own origin lookup.
        if store.session(withID: sessionID)?.remoteHost != nil {
            guard let window = library.windowID(for: store) else {
                return .failure(MessagePaneError("session \(sessionID.uuidString) is remote but its window is not open"))
            }
            return .failure(MessagePaneError("session \(sessionID.uuidString) is remote", kind: .remote, detail: window.uuidString))
        }
        return store.messagePane(session: sessionID, pane: address.pane)
    }

    func messagePanes(workspace: String?, window: String?) -> Result<[MessageRecipient], MessagePaneError> {
        guard let workspace else { return .success(openStores().flatMap { $0.messageRecipients() }) }
        var panes: [MessageRecipient] = []
        let response = resolver.resolveWorkspace(workspace, window: window) { store, id in
            panes = store.messageRecipients(workspace: id)
            return ControlResponse(ok: true)
        }
        guard response.ok else { return .failure(MessagePaneError(response.error ?? "no such workspace: \(workspace)")) }
        return .success(panes)
    }

    func messageQueued(_ messageID: UUID, from: String?, to recipients: [MessageRecipient]) {
        let stores = openStores()
        for recipient in recipients {
            for store in stores where store.emitMessageQueued(messageID, from: from, to: recipient) {
                store.publishMessageQueued(messageID, from: from, to: recipient)
                break
            }
        }
    }

    /// messageForward uses the catalog channel or the attached session's origin control channel.
    func messageForward(window: UUID, request: ControlRequest) async -> ControlResponse {
        var request = request
        guard var args = request.args else {
            return ControlResponse(ok: false, error: "cannot forward \(request.cmd.rawValue)")
        }
        var route: MessageRoute?
        func translate(_ text: String) throws(MessagePaneError) -> String {
            guard let address = try? MessageAddress.recipient(text),
                  case .success(let (store, id)) = resolver.resolveSessionTarget(address.session, window: nil),
                  library.windowID(for: store) == window, let session = store.session(withID: id),
                  let (next, target) = messageOrigin(session, pane: address.pane, window: window) else {
                throw MessagePaneError("no remote pane for \(text)")
            }
            if let route, !route.shares(next) {
                throw MessagePaneError("a message command cannot address panes on different origins")
            }
            route = next
            return target
        }
        do {
            if let recipients = args.recipients {
                var translated: [String] = []
                for recipient in recipients { translated.append(try translate(recipient)) }
                args.recipients = translated
            }
            if let identity = args.identity { args.identity = try translate(identity) }
        } catch {
            return ControlResponse(ok: false, error: error.message)
        }
        request.args = args
        guard let route else { return ControlResponse(ok: false, error: "no remote address to forward") }
        let pool: SshConnectionPool?
        switch route {
        case .catalog(let id, _): pool = remoteCatalogs[id]?.pool
        case .attached: pool = nil
        }
        guard let (words, operands) = MessageForward.arguments(for: request) else {
            return ControlResponse(ok: false, error: "cannot forward \(request.cmd.rawValue)")
        }
        let host = route.host
        if MessageForward.hasNewlineOperand(words: words, operands: operands) {
            return ControlResponse(ok: false, error: "message text must be one line: a newline cannot cross a host yet")
        }
        let slot = pool?.leaseOnce()
        defer { if let slot { pool?.release(slot.user) } }
        if let pool, let slot, !(await pool.ready(slot.path)) {
            return ControlResponse(ok: false, error: "the ssh connection to \(host) is not ready")
        }
        guard let argv = try? RemoteSession.controlCommand(host: host, arguments: words, operands: operands,
                                                           controlPath: slot?.path) else {
            return ControlResponse(ok: false, error: "cannot reach \(host)")
        }
        let result = await remoteRunner.run(argv, deadline: Self.messageForwardTimeout)
        if result.status == 64, result.stdout.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return ControlResponse(ok: false, error: "\(host) refused the message command; its agtermctl is too old, update agterm-server")
        }
        guard let reply = try? JSONDecoder().decode(ControlResponse.self, from: Data(result.stdout.utf8)) else {
            if let note = MessageForward.noAnswerNote(for: request) {
                return .unknownOutcome("\(host) did not answer the message command; \(note)")
            }
            return ControlResponse(ok: false, error: "\(host) did not answer the message command")
        }
        return MessageForward.markRemote(reply)
    }

    /// messageForwardTimeout is how long the Mac waits for the far agtermctl's reply before the outcome is unknown.
    private static let messageForwardTimeout: TimeInterval = 15

    private func messageOrigin(_ session: Session, pane: MessageAddress.Pane, window: UUID) -> (MessageRoute, String)? {
        if let origin = AttachedOrigin(session) {
            return origin.messageAddress(of: session, pane: pane).map { (.attached(origin), $0) }
        }
        guard session.remoteHost != nil, remoteCatalogs[window] != nil,
              let host = library.windows.first(where: { $0.id == window })?.source?.host else { return nil }
        return (.catalog(window, host: host), "\(session.id.uuidString):\(pane.text)")
    }

    private func openStores() -> [AppStore] {
        library.openIDs().compactMap { library.store(for: $0) }
    }
}
