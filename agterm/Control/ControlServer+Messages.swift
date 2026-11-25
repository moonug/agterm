import agtermCore
import Foundation

extension ControlServer {
    /// resolveMessagePane resolves the session across every open window, never the frontmost default for an
    /// id, then asks its store for the live pane.
    func resolveMessagePane(_ address: MessageAddress) -> Result<MessageRecipient, MessagePaneError> {
        let store: AppStore
        let sessionID: UUID
        switch resolver.resolveSessionTarget(address.session, window: nil) {
        case .failure(let response): return .failure(MessagePaneError(response.error ?? "no such session: \(address.session)"))
        case .success(let resolved): (store, sessionID) = resolved
        }
        // a remote row is its host's pane: answer remote with the owning window so msg commands forward there.
        // The same agtermctl channel serves agterm-server and another Mac alike.
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

    /// messageForward runs the msg command on the host of remote window `window`, over the same ssh
    /// `agtermctl` channel its catalog uses. The answer comes back verbatim.
    func messageForward(window: UUID, request: ControlRequest) async -> ControlResponse {
        guard let link = remoteCatalogs[window],
              let host = library.windows.first(where: { $0.id == window })?.source?.host else {
            return ControlResponse(ok: false, error: "window \(window.uuidString) is not a remote window")
        }
        guard let (words, operands) = MessageForward.arguments(for: request) else {
            return ControlResponse(ok: false, error: "cannot forward \(request.cmd.rawValue)")
        }
        let slot = link.pool.leaseOnce()
        defer { link.pool.release(slot.user) }
        guard await link.pool.ready(slot.path) else {
            return ControlResponse(ok: false, error: "the ssh connection to \(host) is not ready")
        }
        guard let argv = try? RemoteSession.controlCommand(host: host, arguments: words, operands: operands,
                                                           controlPath: slot.path) else {
            return ControlResponse(ok: false, error: "cannot reach \(host)")
        }
        let result = await remoteRunner.run(argv, deadline: 15)
        if result.status == 64, result.stdout.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return ControlResponse(ok: false, error: "\(host) refused the message command; its agtermctl is too old, update agterm-server")
        }
        guard let reply = try? JSONDecoder().decode(ControlResponse.self, from: Data(result.stdout.utf8)) else {
            return ControlResponse(ok: false, error: "\(host) did not answer the message command")
        }
        return reply
    }

    private func openStores() -> [AppStore] {
        library.openIDs().compactMap { library.store(for: $0) }
    }
}
