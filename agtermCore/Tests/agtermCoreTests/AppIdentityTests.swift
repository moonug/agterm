import Foundation
import Testing
@testable import agtermCore

struct AppIdentityTests {
    @Test func capabilitiesAreExplicitAndBackwardCompatible() throws {
        let old = try JSONDecoder().decode(AppIdentity.self, from: Data(#"{"version":"1"}"#.utf8))
        #expect(old.capabilities == nil)
        #expect(AppIdentity(version: "1").capabilities == nil)
        let identity = AppIdentity(version: "1", recordedCommit: "unknown", capabilities: ["session.new.split-command"])
        #expect(identity.commit == nil)
        #expect(try JSONDecoder().decode(AppIdentity.self, from: JSONEncoder().encode(identity)) == identity)
    }
    @Test func appIdentityDropsAnUnrecordedCommit() {
        for raw in [nil, "", "unknown"] as [String?] {
            #expect(AppIdentity(version: "0.24.0", recordedCommit: raw).commit == nil)
        }
        #expect(AppIdentity(version: "0.24.0", recordedCommit: "a1b2c3d").commit == "a1b2c3d")
    }
}
