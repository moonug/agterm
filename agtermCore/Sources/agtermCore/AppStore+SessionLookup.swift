import Foundation

extension AppStore {
    /// The workspace that owns the given session, if any.
    public func workspace(forSession sessionID: UUID) -> Workspace? {
        guard let location = location(ofSession: sessionID) else { return nil }
        return workspaces[location.workspaceIndex]
    }

    /// The session with the given id across all workspaces, if any.
    public func session(withID sessionID: UUID) -> Session? {
        for workspace in workspaces {
            if let session = workspace.sessions.first(where: { $0.id == sessionID }) { return session }
        }
        return nil
    }

    func location(ofSession sessionID: UUID) -> (workspaceIndex: Int, sessionIndex: Int)? {
        for (wi, workspace) in workspaces.enumerated() {
            if let si = workspace.sessions.firstIndex(where: { $0.id == sessionID }) { return (wi, si) }
        }
        return nil
    }

    func orderedSessionIDs(matching ids: Set<UUID>) -> [UUID] {
        workspaces.flatMap(\.sessions).map(\.id).filter { ids.contains($0) }
    }

    /// Picks the next selection after removing the session at `location`: the session that shifted into the
    /// removed slot, else the previous one in that workspace, else the first session of any remaining one.
    func reselectionTarget(after location: (workspaceIndex: Int, sessionIndex: Int)) -> UUID? {
        let sessions = workspaces[location.workspaceIndex].sessions
        if location.sessionIndex < sessions.count { return sessions[location.sessionIndex].id }
        if location.sessionIndex > 0, !sessions.isEmpty {
            return sessions[min(location.sessionIndex - 1, sessions.count - 1)].id
        }
        for workspace in workspaces {
            if let first = workspace.sessions.first { return first.id }
        }
        return nil
    }
}
