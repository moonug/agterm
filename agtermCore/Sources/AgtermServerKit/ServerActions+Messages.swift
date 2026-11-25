import Foundation
import agtermCore

extension ServerActions {
    /// messageQueued reaches each Mac viewing a recipient's session as a `message` frame, which carries no body.
    public func messageQueued(_ messageID: UUID, from: String?, to recipients: [MessageRecipient]) {
        recipients.forEach { store.publishMessageQueued(messageID, from: from, to: $0) }
    }

    public func resolveMessagePane(_ address: MessageAddress) -> Result<MessageRecipient, MessagePaneError> {
        switch resolveSession(address.session) {
        case .failure(let refusal):
            // a session a subscribed Mac lists in its mail registry is that Mac's pane: answer remote with the
            // catalog id, this server's only window id, so the shared dispatcher forwards over the catalog stream.
            if let id = UUID(uuidString: address.session), streams.catalogHub.mailServes(id) != nil {
                return .failure(MessagePaneError("session \(address.session) is on a viewing Mac", kind: .remote,
                                                detail: streams.catalogID.uuidString))
            }
            return .failure(MessagePaneError(refusal.response.error ?? "no such session: \(address.session)"))
        case .success(let session): return store.messagePane(session: session.id, pane: address.pane)
        }
    }

    /// messageForward runs a msg command addressed to a viewing Mac's pane over that Mac's catalog stream.
    public func messageForward(window: UUID, request: ControlRequest) async -> ControlResponse {
        guard window == streams.catalogID else {
            return ControlResponse(ok: false, error: "window \(window.uuidString) is not this server's catalog")
        }
        guard let session = mailForwardTarget(request) else {
            return ControlResponse(ok: false, error: "cannot find the pane the message command addresses")
        }
        guard let subscriber = streams.catalogHub.mailServes(session) else {
            return ControlResponse(ok: false, error: streams.catalogHub.hasMailPeer
                ? "no viewing Mac currently lists pane \(session.uuidString)"
                : "the connected Mac predates cross-host mail, update agterm")
        }
        return await streams.mailForward(to: subscriber, request: request)
    }

    /// mailForwardTarget is the session a forwarded msg command addresses: a send's first recipient, a read or
    /// ack's identity.
    private func mailForwardTarget(_ request: ControlRequest) -> UUID? {
        let args = request.args ?? ControlArgs()
        guard let text = request.cmd == .msgSend ? args.recipients?.first : args.identity,
              let address = request.cmd == .msgSend ? try? MessageAddress.recipient(text) : try? MessageAddress.identity(text)
        else { return nil }
        return UUID(uuidString: address.session)
    }

    public func messagePanes(workspace: String?, window: String?) -> Result<[MessageRecipient], MessagePaneError> {
        if let refusal = windowRefusal(window) { return .failure(MessagePaneError(refusal.error ?? "window not found")) }
        guard let workspace else { return .success(store.messageRecipients()) }
        switch resolveWorkspace(workspace) {
        case .failure(let refusal): return .failure(MessagePaneError(refusal.response.error ?? "no such workspace: \(workspace)"))
        case .success(let id): return .success(store.messageRecipients(workspace: id))
        }
    }
}
