import Darwin

enum GhosttyResourceLimits {
    static let childFileDescriptorLimit: rlim_t = 10_240

    static func cappedSoftFileDescriptorLimit(current: rlim_t) -> rlim_t {
        min(current, childFileDescriptorLimit)
    }

    static func lowerSoftFileDescriptorLimit() -> rlimit? {
        var original = rlimit()
        guard getrlimit(RLIMIT_NOFILE, &original) == 0,
              original.rlim_cur > childFileDescriptorLimit else { return nil }

        var limited = original
        limited.rlim_cur = cappedSoftFileDescriptorLimit(current: original.rlim_cur)
        guard setrlimit(RLIMIT_NOFILE, &limited) == 0 else { return nil }
        return original
    }

    static func restoreSoftFileDescriptorLimit(_ original: rlimit?) {
        guard var original else { return }
        _ = setrlimit(RLIMIT_NOFILE, &original)
    }
}
