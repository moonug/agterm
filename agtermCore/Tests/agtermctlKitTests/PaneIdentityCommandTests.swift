import ArgumentParser
import Foundation
import Testing
import agtermCore
@testable import agtermctlKit

struct PaneIdentityCommandTests {
    @Test func typeCarriesGuard() throws {
        let token = UUID().uuidString
        let parsed = try Agtermctl.parseAsRoot(["session", "type", "hello", "--pane", "right", "--expect-pane-id", token])
        let command = try #require(parsed as? any RequestCommand)
        #expect(try command.makeRequest().args?.expectedPaneID == token)
        #expect(try command.makeRequest().args?.pane == "right")
    }

    @Test func typeRejectsMalformedTokenAndImplicitOrScratchPane() {
        let token = UUID().uuidString
        for arguments in [["--pane", "left", "--expect-pane-id", ""], ["--pane", "right", "--expect-pane-id", "bad"],
                          ["--expect-pane-id", token], ["--pane", "scratch", "--expect-pane-id", token]] {
            #expect(throws: (any Error).self) { try Agtermctl.parseAsRoot(["session", "type", "hello"] + arguments) }
        }
    }
}
