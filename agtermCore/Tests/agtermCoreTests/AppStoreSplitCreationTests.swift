import Foundation
import Testing
@testable import agtermCore

@MainActor
struct AppStoreSplitCreationTests {
    @Test func pairIsCompleteInInitialSnapshotWithoutSelectingIt() throws {
        let store = makeStore()
        let workspace = store.addWorkspace(name: "work")
        let source = try #require(store.addSession(toWorkspace: workspace.id, cwd: "/source"))
        let recency = store.sessionRecency.items
        let pair = try #require(store.addSession(toWorkspace: workspace.id, cwd: "/pair",
                                                command: "left", splitCommand: "right", wait: true, at: 1, select: false))
        #expect(store.selectedSessionID == source.id)
        #expect(store.sessionRecency.items == recency)
        #expect(store.workspaces.first?.sessions.map(\.id) == [source.id, pair.id])
        #expect(source.initialCommand == nil)
        #expect(!source.hasSplit)
        #expect(pair.hasSplit && pair.isSplit && !pair.splitFocused)
        #expect(pair.splitAxis == .leftRight)
        #expect(pair.splitPaneIdentity != nil && pair.splitPaneIdentity != pair.paneIdentity)
        let snapshot = try #require(store.snapshot().workspaces.first?.sessions.last)
        #expect(snapshot.initialCommand == "left")
        #expect(snapshot.splitInitialCommand == "right")
        #expect(snapshot.cwd == "/pair" && snapshot.splitCwd == "/pair")
        #expect(snapshot.commandWait == true && snapshot.splitCommandWait == nil)
        #expect(snapshot.isSplit == true && snapshot.hasSplit == true && snapshot.splitAxis == .leftRight)
        #expect(snapshot.paneIdentity == pair.paneIdentity)
        #expect(snapshot.splitPaneIdentity == pair.splitPaneIdentity)
        pair.currentCwd = "/changed"
        #expect(pair.cwd(for: .right) == "/pair")
    }

    @Test(arguments: [true, false])
    func earlyExitPreservesUnmountedSibling(primaryExits: Bool) throws {
        let store = makeStore()
        let workspace = store.addWorkspace(name: "work")
        let pair = try #require(store.addSession(toWorkspace: workspace.id, cwd: "/pair",
                                                command: "left", splitCommand: "right", wait: true))
        let survivorID = primaryExits ? pair.splitPaneIdentity : pair.paneIdentity
        if primaryExits {
            pair.surface = SpySurface()
            pair.currentCwd = "/changed"
            store.closePrimaryPane(pair.id)
        } else {
            pair.splitSurface = SpySurface()
            store.closeSplitPane(pair.id)
        }
        #expect(store.session(withID: pair.id) === pair)
        #expect(pair.surface == nil && pair.splitSurface == nil)
        #expect(!pair.hasSplit && !pair.isSplit)
        #expect(pair.paneIdentity == survivorID)
        #expect(pair.initialCommand == (primaryExits ? "right" : "left"))
        #expect(pair.commandWait == !primaryExits)
        #expect(pair.effectiveCwd == "/pair")
        #expect(pair.splitInitialCommand == nil && pair.splitPaneIdentity == nil)
        let snapshot = try #require(store.snapshot().workspaces.first?.sessions.last)
        #expect(snapshot.cwd == "/pair")
        #expect(snapshot.initialCommand == pair.initialCommand)
        store.closePrimaryPane(pair.id)
        #expect(store.session(withID: pair.id) == nil)
    }

    @Test func earlyPromotionMovesPendingForegroundRestore() throws {
        let store = makeStore()
        let workspace = store.addWorkspace(name: "work")
        let pair = try #require(store.addSession(toWorkspace: workspace.id, cwd: "/pair",
                                                command: "left", splitCommand: "right"))
        pair.wasRestored = true
        pair.pendingForegroundCommand = ["departed", "--old"]
        pair.pendingSplitForegroundCommand = ["survivor", "--resume", "right-session"]
        store.closePrimaryPane(pair.id)
        #expect(pair.takePendingForegroundCommand(pane: .left) == ["survivor", "--resume", "right-session"])
        #expect(pair.takePendingForegroundCommand(pane: .right) == nil)
        #expect(pair.takePendingForegroundCommand(pane: .left) == nil)
    }
}
