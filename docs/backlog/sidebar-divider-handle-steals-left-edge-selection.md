---
worth: later
where: agterm/Views/WindowContentView.swift:341
added: 2026-08-12
---
# sidebar divider grab handle steals a text selection started at the pane's left edge

`sidebarDivider` centers a 12pt invisible grab handle on the 1pt line, so roughly 5pt of live resize
target sits over the terminal. A drag that starts on the first character cell of a line resizes the
sidebar instead of selecting text. Measured hit band, sidebar ending at x=100 and the line at 100..101:
chrome answers `hitTest` from x=94 to x=105.

Moving the band left does not work, and three geometries were tried and rejected by hand:

- 9pt sidebar / 3pt terminal (`.offset` shifted): terminal-side catches persisted, and the cursor began
  flickering near the line.
- 6pt sidebar / 0pt terminal (trailing-aligned, no `.offset`): selection was fixed, and the cursor
  flickered worse. This is the one that identifies the cause.
- 8pt sidebar / 2.5pt terminal: rejected before the flicker was re-checked.

The band that AppKit hit-tests is the band SwiftUI drags from, and the cursor is painted from the same
view's `onContinuousHover`. Shrinking the terminal-side reach therefore drags the band's edge onto the
line, which is exactly where the pointer aims, and every sub-pixel crossing repaints. From an in-app probe
logging `NSCursor` transitions on an 8ms timer against the 6/0 build, pointer resting near the line:

```
9251.535 x=220.9  hover active  -> divider resizeLR
9251.551 x=221.1  hover ended                  <- 0.2pt of movement
9251.557 x=221.1  CURSOR -> iBeam              <- surface repaints
9252.069 x=221.0  hover active -> resizeLR
9252.077 x=221.0  CURSOR -> arrow              <- SwiftUI writes arrow after our set
9252.084 x=221.0  CURSOR -> resizeLR           <- deferred re-assert wins it back, 15ms later
```

The centered handle avoids all of this because the line sits mid-band with ~5pt of margin either side, so
hand oscillation never leaves it. Two further constraints found on the way: `.offset` does carry the
AppKit hit region with it (verified against a replica app, not assumed), and `zIndex(1)` is load-bearing
for any terminal-side reach, since the detail column otherwise shadows it and the band stops at the line.

Fixing this properly means decoupling the cursor from the grab: an `NSTrackingArea` probe painting ↔ over
a symmetric band, plus a geometric sidebar-divider band in `GhosttySurfaceView.ownsPointer` so the pane
declines there, which is the `SplitProbeView` treatment in `SplitRatioAccessor.swift`. That contradicts
the no-guessed-widths rule `ownsPointer` documents, and it is a large change for an occasional misfire,
which is why the geometry-only route was tried first and the whole attempt then dropped.
