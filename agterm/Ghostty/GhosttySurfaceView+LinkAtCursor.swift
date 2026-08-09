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
    /// covers the cursor cell. Routes through the SAME `openLink` → `LinkPolicy` decision as an OSC-8
    /// hyperlink click (web/mailto opened, local `file://` revealed, anything else ignored), so a resolved
    /// rule never gains powers the explicit-link path lacks. Returns the matched raw text so a caller can
    /// show feedback, or nil when the cursor isn't in the surface, nothing matches, or policy ignores it.
    /// Driven by the `cmd+opt+o` keymap binding AND ⌘+click (both wired by `mouseDown`); same code path.
    @discardableResult
    func openLinkAtMouseCursor() -> String? {
        guard let grid = surfaceGrid else { return nil }
        guard let mouse = lastReportedMousePoint, mouse.x >= 0, mouse.y >= 0 else { return nil }
        // the reported mouse point is in view POINTS (flipped); cell sizes are PHYSICAL pixels, so scale.
        let scale = window?.backingScaleFactor ?? 2.0
        let col = Int((mouse.x * scale) / CGFloat(grid.cellW))
        let row = Int((mouse.y * scale) / CGFloat(grid.cellH))
        guard col >= 0, col < grid.cols, row >= 0, row < grid.rows else { return nil }
        guard let text = readScreenText(all: false, lines: nil) else { return nil }

        // build each visible row: the plain-text buffer uses `\n` line separators, but a terminal fills
        // each row to the column count, so pad short rows to `cols` so col ↔ char offset is exact.
        var rows: [String] = []
        for rowText in text.components(separatedBy: "\n").prefix(grid.rows) {
            rows.append(rowText.count >= grid.cols ? rowText : rowText + String(repeating: " ", count: grid.cols - rowText.count))
        }
        guard rows.indices.contains(row) else { return nil }

        let configDir = ConfigPaths.configDirectory(
            setting: nil,
            stateDir: ProcessInfo.processInfo.environment["AGTERM_STATE_DIR"],
            home: FileManager.default.homeDirectoryForCurrentUser)
        let rules = LinkRules.load(configDirectory: configDir).rules
        guard let raw = LinkRules.firstMatch(rules: rules, cellText: rows[row], offset: col) else { return nil }
        guard LinkPolicy.disposition(for: raw) != .ignore else { return nil }
        openLink(raw)
        return raw
    }
}
