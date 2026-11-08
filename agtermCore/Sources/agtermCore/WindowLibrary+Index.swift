import Foundation

extension WindowLibrary {
    /// saveIndex writes `windows.json`: ordered window list with open flags, plus the frontmost id. A
    /// failure is logged and leaves `indexUnsaved` set until a later write lands.
    @discardableResult
    public func saveIndex() -> Bool {
        let entries = windows.map { WindowEntry(id: $0.id, name: $0.name, isOpen: stores[$0.id] != nil) }
        let index = WindowsIndex(frontmost: frontmostWindowID, windows: entries)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            try encoder.encode(index).write(to: indexURL, options: .atomic)
            indexUnsaved = false
            return true
        } catch {
            log("saveIndex failed: \(error)")
            indexUnsaved = true
            return false
        }
    }

    /// retryUnsavedIndex rewrites a failed index once a snapshot save shows the disk takes writes again.
    /// Without it a stale index stands until the next window create, close, rename or delete, and a
    /// crash before that orphans the window file the index never named.
    func retryUnsavedIndex() {
        if indexUnsaved { saveIndex() }
    }

    /// saveAllChecked writes every open window's snapshot and then the index, attempting both, and
    /// reports whether all of them landed.
    @discardableResult
    public func saveAllChecked() -> Bool {
        let snapshots = saveAllOpenChecked()
        let index = saveIndex()
        return snapshots && index
    }

    /// Reads `windows.json`; a missing/corrupt/version-mismatched file reads as nil, so the caller falls
    /// through to recovery/migration/seeding.
    func loadIndex() -> WindowsIndex? {
        guard let data = try? Data(contentsOf: indexURL) else { return nil }
        guard let index = try? JSONDecoder().decode(WindowsIndex.self, from: data) else { return nil }
        guard index.version == WindowsIndex.currentVersion, !index.windows.isEmpty else { return nil }
        return index
    }
}
