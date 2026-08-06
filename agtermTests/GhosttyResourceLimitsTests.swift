import XCTest
@testable import agterm

final class GhosttyResourceLimitsTests: XCTestCase {
    func testCappedLimitLeavesNormalValuesUntouched() {
        XCTAssertEqual(GhosttyResourceLimits.cappedSoftFileDescriptorLimit(current: 256), 256)
        XCTAssertEqual(GhosttyResourceLimits.cappedSoftFileDescriptorLimit(current: 10_240), 10_240)
    }

    func testCappedLimitBoundsLargeValues() {
        XCTAssertEqual(GhosttyResourceLimits.cappedSoftFileDescriptorLimit(current: 1_050_880), 10_240)
    }
}
