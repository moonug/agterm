import AppKit
import Foundation
import agtermCore
import GhosttyKit

extension GhosttySurfaceView {
    /// The cell grid size for a surface, from libghostty: columns × rows plus the pixel cell size.
    private struct SurfaceGrid {
        let cols: Int
        let rows: Int
        let cellW: Int
        let cellH: Int
    }

    /// The live grid size, nil when the surface isn't realized yet (no `ghostty_surface_size`).
    private var surfaceGrid: SurfaceGrid? {
        guard let surface else { return nil }
        let size = ghostty_surface_size(surface)
        guard size.columns > 0, size.rows > 0, size.cell_width_px > 0, size.cell_height_px > 0 else { return nil }
        return SurfaceGrid(cols: Int(size.columns), rows: Int(size.rows),
                           cellW: Int(size.cell_width_px), cellH: Int(size.cell_height_px))
    }

    /// Open the link under the mouse cursor: the first `link-rules.conf` (or default) rule whose match
    /// covers the cursor cell, spanning soft-wrapped rows. Routes through the SAME `openLink` →
    /// `LinkPolicy` decision as an OSC-8 hyperlink click (web/mailto opened, local `file://` revealed,
    /// anything else ignored), so a resolved rule never gains powers the explicit-link path lacks.
    /// Returns the matched raw text so a caller can show feedback, or nil when the cursor isn't in the
    /// surface, nothing matches, or policy ignores it. Driven by the `cmd+opt+o` keymap binding AND
    /// ⌘+click (both wired by `mouseDown`); same code path.
    @discardableResult
    func openLinkAtMouseCursor() -> String? {
        guard let grid = surfaceGrid else { return nil }
        guard let mouse = lastReportedMousePoint, mouse.x >= 0, mouse.y >= 0 else { return nil }
        // the reported mouse point is in view POINTS (flipped); cell sizes are PHYSICAL pixels, so scale.
        // ghostty draws window-padding inside the surface, so the viewport origin comes off before the
        // division — without it a click resolves about a cell right/below the glyph under it.
        let scale = window?.backingScaleFactor ?? 2.0
        let pad = viewportPaddingPoints()
        let col = max(0, Int(((mouse.x - pad.x) * scale) / CGFloat(grid.cellW)))
        let row = max(0, Int(((mouse.y - pad.y) * scale) / CGFloat(grid.cellH)))
        guard col < grid.cols, row < grid.rows else { return nil }
        guard let text = readScreenText(all: false, lines: nil) else { return nil }
        let lines = text.components(separatedBy: "\n")

        let configDir = ConfigPaths.configDirectory(
            setting: nil,
            stateDir: ProcessInfo.processInfo.environment["AGTERM_STATE_DIR"],
            home: FileManager.default.homeDirectoryForCurrentUser)
        let rules = LinkRules.load(configDirectory: configDir).rules
        guard let raw = LinkRules.urlAtCursor(rules: rules, lines: lines, cols: grid.cols, row: row, col: col) else { return nil }
        guard LinkPolicy.disposition(for: raw) != .ignore else { return nil }
        openLink(raw)
        return raw
    }

    /// The viewport's left/top window-padding in POINTS, measured the way `readCursorColumn` measures it:
    /// at viewport cell (0,0) `tl_px_x`/`tl_px_y` are exactly the padding terms. Zeros when the probe
    /// fails — a degraded measurement costs edge accuracy, not correctness.
    private func viewportPaddingPoints() -> (x: Double, y: Double) {
        guard let surface else { return (0, 0) }
        var sel = ghostty_selection_s()
        let origin = ghostty_point_s(tag: GHOSTTY_POINT_VIEWPORT, coord: GHOSTTY_POINT_COORD_EXACT, x: 0, y: 0)
        sel.top_left = origin
        sel.bottom_right = origin
        sel.rectangle = false
        var probe = ghostty_text_s()
        guard ghostty_surface_read_text(surface, sel, &probe) else { return (0, 0) }
        defer { ghostty_surface_free_text(surface, &probe) }
        guard probe.tl_px_x >= 0, probe.tl_px_y >= 0 else { return (0, 0) }
        return (probe.tl_px_x, probe.tl_px_y)
    }
}
