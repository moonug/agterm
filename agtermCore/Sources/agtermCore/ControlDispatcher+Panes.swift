extension ControlDispatcher {
    /// The outcome of parsing a `--pane` selector: the pane (nil when the selector was absent), or the
    /// rejection response the arm returns as-is. Generic over the pane type, so the role selector and the
    /// overlay selector differ only in which enum they parse into and which rejection they carry.
    enum PaneSelection<Pane> {
        case pane(Pane?)
        case rejected(ControlResponse)
    }

    /// The shared `--pane` selector: nil when absent, the parsed pane when `parse` accepts it, and `error`
    /// as the pinned rejection otherwise. No live session needed either way.
    func parsePane<Pane>(_ raw: String?, error: String,
                         parse: (String) -> Pane?) -> PaneSelection<Pane> {
        guard let raw else { return .pane(nil) }
        guard let parsed = parse(raw) else { return .rejected(ControlResponse(ok: false, error: error)) }
        return .pane(parsed)
    }

    /// The role selector (`session.status`, `session.restore`, `session.paste`). It accepts role and position
    /// aliases; the stable rejection names the canonical `left|right|scratch` read-back values.
    func parsePane(_ raw: String?) -> PaneSelection<StatusPane> {
        parsePane(raw, error: "--pane must be left, right, or scratch") { StatusPane(controlName: $0) }
    }

    /// The surface I/O selector (`session.type`, `session.text`, `font.*`). Same vocabulary as `parsePane`,
    /// so the role and position aliases resolve, but the rejection keeps the per-command
    /// `invalid pane: <value>` these three answer, which the reference records as their difference from
    /// `session.status`/`.restore`. Unifying the wording is a separate decision from parsing the value.
    func parseSurfacePane(_ raw: String?) -> PaneSelection<StatusPane> {
        parsePane(raw, error: "invalid pane: \(raw ?? "")") { StatusPane(controlName: $0) }
    }

    /// The `session.overlay.*` selector (`.open`/`.close`/`.result`/`.copy`/`.text`): absent keeps the
    /// session-wide overlay,
    /// `left`/`right` (and their `primary`/`split` aliases) scope to one pane, `scratch` is rejected — there
    /// being no scratch pane to cover.
    func parseOverlayPane(_ raw: String?) -> PaneSelection<OverlayPane> {
        parsePane(raw, error: PaneOverlayError.invalidPane) { OverlayPane(controlName: $0) }
    }
}
