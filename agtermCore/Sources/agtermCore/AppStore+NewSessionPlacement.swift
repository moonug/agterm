import Foundation

extension AppStore {
    /// newSessionInsertionIndex returns the slot after the selection in `workspaceID` for `afterCurrent`,
    /// or nil to append.
    public func newSessionInsertionIndex(inWorkspace workspaceID: UUID,
                                         placement: AppSettings.NewSessionPlacement) -> Int? {
        guard placement == .afterCurrent, let selectedSessionID,
              let location = sessionLocation(ofSession: selectedSessionID),
              location.workspace == workspaceID else { return nil }
        return location.index + 1
    }
}
