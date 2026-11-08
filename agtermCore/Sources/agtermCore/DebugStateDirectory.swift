import Foundation

/// DebugStateDirectory keeps a Debug build off the deployed app's state when nothing isolates the launch.
public enum DebugStateDirectory {
    public static let environmentKey = "AGTERM_STATE_DIR"

    /// adopted returns a sibling of `liveDirectory`, nil when the launch names a non-empty directory.
    public static func adopted(environment: [String: String], liveDirectory: URL) -> String? {
        if let explicit = environment[environmentKey], !explicit.isEmpty { return nil }
        return sibling(of: liveDirectory)
    }

    /// configStateDirectory is the state directory `ConfigPaths.configDirectory` should see: nil for the
    /// `agterm-debug` sibling, so an unisolated Debug launch reads the default config directory. The test
    /// is the exact path, so a launch naming that sibling explicitly, Release included, gets the same.
    public static func configStateDirectory(environment: [String: String], liveDirectory: URL) -> String? {
        guard let stateDir = environment[environmentKey], stateDir != sibling(of: liveDirectory) else { return nil }
        return stateDir
    }

    private static func sibling(of liveDirectory: URL) -> String {
        liveDirectory.deletingLastPathComponent().appendingPathComponent("agterm-debug", isDirectory: true).path
    }
}
