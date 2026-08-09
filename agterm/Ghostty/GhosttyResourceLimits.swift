import Darwin
import os

private let logger = Logger(subsystem: "com.umputun.agterm", category: "GhosttyResourceLimits")

enum GhosttyResourceLimits {
    static let childFileDescriptorLimit: rlim_t = 10_240

    static func cappedSoftFileDescriptorLimit(current: rlim_t) -> rlim_t {
        min(current, childFileDescriptorLimit)
    }

    static func lowerSoftFileDescriptorLimit() -> rlimit? {
        var original = rlimit()
        guard getrlimit(RLIMIT_NOFILE, &original) == 0 else {
            logger.error("getrlimit(RLIMIT_NOFILE) failed: errno=\(errno, privacy: .public)")
            return nil
        }
        guard original.rlim_cur > childFileDescriptorLimit else { return nil }

        var limited = original
        limited.rlim_cur = cappedSoftFileDescriptorLimit(current: original.rlim_cur)
        guard setrlimit(RLIMIT_NOFILE, &limited) == 0 else {
            logger.error("setrlimit(RLIMIT_NOFILE) failed: errno=\(errno, privacy: .public)")
            return nil
        }
        return original
    }

    static func restoreSoftFileDescriptorLimit(_ original: rlimit?) {
        guard var original else { return }
        guard setrlimit(RLIMIT_NOFILE, &original) == 0 else {
            logger.error("restoring RLIMIT_NOFILE failed: errno=\(errno, privacy: .public)")
            return
        }
    }
}
