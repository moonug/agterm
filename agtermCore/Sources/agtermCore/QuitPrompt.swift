/// Pure builder for the quit-confirmation alert text. Host-free so the pluralization is unit-tested
/// without an app host; the AppKit `NSAlert` lives in the app target's `AppDelegate`.
public enum QuitPrompt {
    /// The informative line for "Quit Agterm?", reporting how many windows and sessions the quit closes so
    /// the loss is explicit (matching the workspace/window delete confirmations). Singular/plural per count.
    /// `mode` is the launch decision's active mode; settings changes apply after restart.
    public static func message(windows: Int, sessions: Int, mode: RestoreMode) -> String {
        let windowClause = windows == 1 ? "1 window" : "\(windows) windows"
        let sessionClause = sessions == 1 ? "1 session" : "\(sessions) sessions"
        if mode == .live { return "This closes \(windowClause) and \(sessionClause)." }
        return "This closes \(windowClause) and \(sessionClause), ending all running shells."
    }
}
