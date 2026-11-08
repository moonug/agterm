import ArgumentParser
import Testing
import agtermCore
@testable import agtermctlKit

struct PairedSessionCommandTests {
    @Test func sessionNewPairedCommands() throws {
        let expected = ControlRequest(cmd: .sessionNew, args: ControlArgs(cwd: "/pair", noSelect: true,
            command: "left", splitCommand: "right", wait: true, after: "source"))
        let parsed = try Agtermctl.parseAsRoot(["session", "new", "--command", "left", "--split-command", "right", "--wait",
                                               "--cwd", "/pair", "--after", "source", "--no-select"])
        let command = try #require(parsed as? any RequestCommand)
        #expect(try command.makeRequest() == expected)
        #expect(validationMessage(["session", "new", "--split-command", "right"]) == "--split-command requires --command")
        for invalid in ["", " \n", "bad\0command"] {
            #expect(validationMessage(["session", "new", "--command", invalid, "--split-command", "right"]) != nil)
            #expect(validationMessage(["session", "new", "--command", "left", "--split-command", invalid]) != nil)
        }
    }

    private func validationMessage(_ arguments: [String]) -> String? {
        do {
            _ = try Agtermctl.parseAsRoot(arguments)
            return nil
        } catch {
            return Agtermctl.message(for: error)
        }
    }
}
