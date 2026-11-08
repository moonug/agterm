import Foundation
import Testing
@testable import agtermCore

@MainActor
struct ControlPaneIdentityTests {
    @Test func typeThreadsExpectedIdentityWithoutChangingLegacyOptions() async {
        let token = UUID().uuidString
        for (name, pane) in [("left", StatusPane.left), ("right", .right)] {
            let actions = MockControlActions()
            _ = await ControlDispatcher(actions: actions).dispatch(ControlRequest(cmd: .sessionType, target: "s",
                args: ControlArgs(text: "hello", pane: name, expectedPaneID: token)))
            #expect(actions.calls == [.sessionType(target: "s", window: nil,
                ControlSessionTypeOptions(text: "hello", select: false, pane: pane, expectedPaneID: token))])
        }
        #expect(ControlSessionTypeOptions(text: "hello", select: false, pane: nil).expectedPaneID == nil)
    }

    @Test func typeRejectsInvalidGuardBeforeActions() async {
        let token = UUID().uuidString
        for (pane, id) in [("left", ""), ("right", "bad"), ("scratch", token), (nil, token)] as [(String?, String)] {
            let actions = MockControlActions()
            let response = await ControlDispatcher(actions: actions).dispatch(ControlRequest(cmd: .sessionType,
                args: ControlArgs(text: "hello", pane: pane, expectedPaneID: id)))
            #expect(response?.ok == false)
            #expect(actions.calls.isEmpty)
        }
    }

    @Test func oldSessionNodeOmitsAndDecodesAbsentIdentities() throws {
        let node = ControlSessionNode(id: "s", name: "s", cwd: "/tmp", active: true, split: false, backedByZmx: nil)
        let data = try JSONEncoder().encode(node)
        let json = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(json["paneID"] == nil && json["splitPaneID"] == nil)
        let decoded = try JSONDecoder().decode(ControlSessionNode.self, from: data)
        #expect(decoded.paneID == nil && decoded.splitPaneID == nil)
    }

    @Test func treeTokensFollowLogicalPanesThroughHidingAndPromotion() throws {
        let store = makeStore()
        let workspace = store.addWorkspace(name: "work")
        let session = try #require(store.addSession(toWorkspace: workspace.id, cwd: "/tmp", command: "left", splitCommand: "right"))
        let leftID = session.paneIdentity.uuidString
        let rightID = try #require(session.splitPaneIdentity?.uuidString)
        for shown in [true, false] {
            store.setSplitVisibility(session.id, shown: shown)
            let node = try #require(store.controlTree().workspaces.first?.sessions.first)
            #expect(node.paneID == leftID && node.splitPaneID == rightID)
            #expect(try JSONDecoder().decode(ControlSessionNode.self, from: JSONEncoder().encode(node)) == node)
        }
        store.closePrimaryPane(session.id)
        let promoted = try #require(store.controlTree().workspaces.first?.sessions.first)
        #expect(promoted.paneID == rightID)
        #expect(promoted.splitPaneID == nil)
    }
}
