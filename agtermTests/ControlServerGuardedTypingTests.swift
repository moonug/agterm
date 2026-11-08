import AppKit
import XCTest
@testable import agterm
import agtermCore

/// Hosted coverage for `ControlServer`'s strict pane-slot guard, which needs the real app target for
/// window resolution and the store registry.
@MainActor
final class ControlServerGuardedTypingTests: XCTestCase {
    private var stateDir: URL!
    private var library: WindowLibrary!
    private var actions: AppActions!
    private var server: ControlServer!

    override func setUp() async throws {
        try await super.setUp()
        await MainActor.run {
            stateDir = FileManager.default.temporaryDirectory
                .appendingPathComponent("agterm-control-guarded-tests-\(UUID().uuidString)", isDirectory: true)
            library = WindowLibrary(directory: stateDir)
            actions = AppActions(library: library)
            server = ControlServer(
                library: library,
                actions: actions,
                settingsModel: SettingsModel(library: library, settingsStore: SettingsStore(directory: stateDir)),
                identity: AppIdentity(version: "9.9.9", commit: "testsha"),
                socketPath: stateDir.appendingPathComponent("control.sock").path
            )
        }
    }

    override func tearDown() async throws {
        await MainActor.run {
            server = nil
            actions = nil
            library = nil
            try? FileManager.default.removeItem(at: stateDir)
            stateDir = nil
        }
        try await super.tearDown()
    }

    private func addSession() throws -> (AppStore, Session) {
        let store = try XCTUnwrap(library.activeStore)
        let owner = try XCTUnwrap(store.currentWorkspaceID)
        return (store, try XCTUnwrap(store.addSession(toWorkspace: owner, cwd: NSHomeDirectory())))
    }

    func testGuardedTypingDeliversOnlyToMatchingExplicitSlot() throws {
        let (store, session) = try addSession()
        store.setSplitVisibility(session.id, shown: true)
        let rightID = try XCTUnwrap(session.splitPaneIdentity)
        let left = GhosttySurfaceView(workingDirectory: "/tmp", env: ["AGTERM_PANE_ID": session.paneIdentity.uuidString])
        let right = GhosttySurfaceView(workingDirectory: "/tmp", env: ["AGTERM_PANE_ID": rightID.uuidString])
        session.surface = left
        session.splitSurface = right
        for (pane, token, expected) in [(StatusPane.left, session.paneIdentity, left), (.right, rightID, right)] {
            var calls = 0
            let response = server.injectGuardedText("hello", into: session.id, store: store, pane: pane,
                expectedPaneID: token.uuidString.lowercased()) { surface, text in
                    calls += 1
                    XCTAssertTrue(surface === expected)
                    XCTAssertEqual(text, "hello")
                    return true
                }
            XCTAssertTrue(response.ok)
            XCTAssertEqual(calls, 1)
        }
    }

    func testGuardedTypingRejectsSwapAndReplacementWithoutSending() throws {
        let (store, session) = try addSession()
        store.setSplitVisibility(session.id, shown: true)
        let leftID = session.paneIdentity
        let rightID = try XCTUnwrap(session.splitPaneIdentity)
        session.surface = GhosttySurfaceView(workingDirectory: "/tmp", env: ["AGTERM_PANE_ID": leftID.uuidString])
        session.splitSurface = GhosttySurfaceView(workingDirectory: "/tmp", env: ["AGTERM_PANE_ID": rightID.uuidString])
        XCTAssertNil(store.swapPanes(session.id))
        var calls = 0
        let send: (GhosttySurfaceView, String) -> Bool = { _, _ in calls += 1; return true }
        for (pane, token) in [(StatusPane.left, leftID), (.right, rightID)] {
            let response = server.injectGuardedText("hello", into: session.id, store: store, pane: pane,
                                                   expectedPaneID: token.uuidString, send: send)
            XCTAssertEqual(response.error, "pane identity mismatch")
        }
        session.surface = GhosttySurfaceView(workingDirectory: "/tmp", env: ["AGTERM_PANE_ID": UUID().uuidString])
        XCTAssertEqual(server.injectGuardedText("hello", into: session.id, store: store, pane: .left,
            expectedPaneID: rightID.uuidString, send: send).error, "pane identity mismatch")
        session.paneIdentity = UUID()
        XCTAssertFalse(server.injectGuardedText("hello", into: session.id, store: store, pane: .left,
            expectedPaneID: rightID.uuidString, send: send).ok)
        XCTAssertEqual(calls, 0)
    }

    func testGuardedTypingRejectsInvalidTokenOrPaneWithoutSending() throws {
        let (store, session) = try addSession()
        var calls = 0
        for (pane, token) in [(StatusPane.left, ""), (.right, "bad"), (.scratch, session.paneIdentity.uuidString),
                              (nil, session.paneIdentity.uuidString)] as [(StatusPane?, String)] {
            let response = server.injectGuardedText("hello", into: session.id, store: store, pane: pane,
                expectedPaneID: token) { _, _ in calls += 1; return true }
            XCTAssertFalse(response.ok)
        }
        XCTAssertEqual(calls, 0)
    }

    func testTypeSessionEnforcesGuardAndDoesNotSelectUnrealizedPane() async throws {
        let (store, session) = try addSession()
        let (_, selected) = try addSession()
        let wrong = await server.typeSession(session.id.uuidString, window: nil,
            options: ControlSessionTypeOptions(text: "hello", select: true, pane: .left, expectedPaneID: UUID().uuidString))
        XCTAssertEqual(wrong.error, "pane identity mismatch")
        let unmounted = await server.typeSession(session.id.uuidString, window: nil,
            options: ControlSessionTypeOptions(text: "hello", select: true, pane: .left, expectedPaneID: session.paneIdentity.uuidString))
        XCTAssertEqual(unmounted.error, "session not realized")
        XCTAssertEqual(store.selectedSessionID, selected.id)
    }
}
