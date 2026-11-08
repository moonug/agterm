import Foundation
import agtermCore

extension ControlServer {
    func typeSession(_ target: String?, window: String?, options: ControlSessionTypeOptions) async -> ControlResponse {
        // resolve first (cross-window without `args.window`), then realize-and-inject: the async
        // bounded-poll realize rules out the synchronous `resolveSession`. Error strings must match `resolve(...)`.
        switch resolver.resolveSessionTarget(target, window: window) {
        case .failure(let response):
            return response
        case .success(let (store, id)):
            if let expectedPaneID = options.expectedPaneID {
                return injectGuardedText(options.text, into: id, store: store, pane: options.pane, expectedPaneID: expectedPaneID)
            }
            let session = store.session(withID: id)
            let pane: StatusPane?
            switch session?.paneAddress(token: options.paneID, pane: options.pane) ?? .pane(options.pane) {
            case .unknownToken(let token): return Self.unknownPaneID(token)
            case .pane(let resolved): pane = resolved
            }
            let token = options.paneID.flatMap { session?.paneRole(forToken: $0) == nil ? nil : $0 }
            var response = await injectText(options.text, into: id, store: store, select: options.select,
                                            pane: pane, token: token)
            if response.ok, response.result?.pane == nil { response.result?.pane = (pane ?? .left).rawValue }
            return response
        }
    }

    /// Identity comparison and delivery must stay synchronous on the main actor: a swap cannot interleave.
    func injectGuardedText(_ text: String, into id: UUID, store: AppStore, pane: StatusPane?, expectedPaneID: String,
                           send: (GhosttySurfaceView, String) -> Bool = { $0.injectAsUserInput(text: $1) }) -> ControlResponse {
        guard let expected = UUID(uuidString: expectedPaneID) else {
            return ControlResponse(ok: false, error: "--expect-pane-id must be a UUID")
        }
        guard pane == .left || pane == .right else {
            return ControlResponse(ok: false, error: "--expect-pane-id requires explicit --pane left or right")
        }
        guard let session = store.session(withID: id) else {
            return ControlResponse(ok: false, error: "session not found")
        }
        let identity = pane == .left ? session.paneIdentity : (session.hasSplit ? session.splitPaneIdentity : nil)
        guard identity == expected else {
            return ControlResponse(ok: false, error: "pane identity mismatch")
        }
        guard let surface = (pane == .left ? session.surface : session.splitSurface) as? GhosttySurfaceView else {
            return ControlResponse(ok: false, error: "session not realized")
        }
        guard UUID(uuidString: surface.paneToken) == expected else {
            return ControlResponse(ok: false, error: "pane identity mismatch")
        }
        guard send(surface, text) else { return ControlResponse(ok: false, error: "session not realized") }
        return ControlResponse(ok: true, result: ControlResult(id: id.uuidString))
    }
}
