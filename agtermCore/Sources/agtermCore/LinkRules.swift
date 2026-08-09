import Foundation

/// Implicit link rules for `open_link_at_cursor` — the agterm analog of wezterm's `hyperlink_rules`.
/// Each rule is a regex whose FIRST match whose range contains the cursor cell is opened; a rule may carry
/// a `format` template substituting `$N` capture groups into a URL, else the matched text is used as-is.
/// The always-on defaults (`https?://`, `mailto:`, `file:`) mirror `LinkPolicy.permittedSchemes` so a
/// resolved URL routes through the same open/reveal/ignore decision as an OSC-8 hyperlink. Host-free
/// (Foundation-only) so it is unit-tested; the config dir is injected. Parsed rules are `Sendable` (the
/// regex is immutable after init), so a reload can swap the rule set across an actor boundary.
public struct LinkRules {
    /// One configured or default rule. `format` uses `$N` for capture groups (1-based), `$0` the whole match.
    public struct Rule: Equatable, Sendable {
        public let regex: NSRegularExpression
        public let format: String?
        public let sourceLine: Int?

        public init(regex: NSRegularExpression, format: String?, sourceLine: Int?) {
            self.regex = regex
            self.format = format
            self.sourceLine = sourceLine
        }
    }

    /// A problem while parsing `link-rules.conf`. `line` is 1-based; `0` is whole-file.
    public struct Diagnostic: Equatable, Sendable {
        public let line: Int
        public let message: String

        public init(line: Int, message: String) {
            self.line = line
            self.message = message
        }
    }

    /// The built-in rules that are always active, independent of the config file.
    public static let defaultPatterns: [(pattern: String, format: String?)] = [
        ("https?://[^[:space:]]+", nil),
        ("mailto:[^[:space:]]+", nil),
        ("file://[^[:space:]]+", nil),
    ]

    /// Build the always-on default rules (web/mail/file). Regexes never throw for these static patterns.
    public static func defaultRules() -> [Rule] {
        defaultPatterns.compactMap { pattern, format in
            guard let regex = try? NSRegularExpression(pattern: pattern) else { return nil }
            return Rule(regex: regex, format: format, sourceLine: nil)
        }
    }

    /// Parse the text of a `link-rules.conf` into user rules plus diagnostics. Grammar, line-based:
    /// blank and `#` lines ignored; a `regex = <pattern>` line opens a rule, an optional following
    /// `format = <template>` line attaches its URL template. A malformed regex is a diagnostic and the
    /// rule is skipped — one bad line never discards the rest. An empty file yields no user rules.
    public static func parse(_ text: String) -> (rules: [Rule], diagnostics: [Diagnostic]) {
        var rules: [Rule] = []
        var diagnostics: [Diagnostic] = []
        var currentPattern: (pattern: String, line: Int)?

        let normalized = text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
        for (index, rawLine) in normalized.split(separator: "\n", omittingEmptySubsequences: false).enumerated() {
            let lineNumber = index + 1
            let line = String(rawLine).trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") { continue }

            if let key = lineKeyValue(line) {
                let (keyName, value) = key
                switch keyName {
                case "regex":
                    // a new regex opens a rule; a previous unterminated one (no format) is closed with a
                    // null format — append it now rather than deferring to the end, keeping order stable.
                    if let prev = currentPattern {
                        appendRule(pattern: prev.pattern, format: nil, line: prev.line,
                                   rules: &rules, diagnostics: &diagnostics)
                    }
                    currentPattern = (value, lineNumber)
                case "format":
                    guard let prev = currentPattern else {
                        diagnostics.append(Diagnostic(line: lineNumber, message: "format without a preceding regex"))
                        break
                    }
                    appendRule(pattern: prev.pattern, format: value, line: prev.line,
                               rules: &rules, diagnostics: &diagnostics)
                    currentPattern = nil
                default:
                    diagnostics.append(Diagnostic(line: lineNumber, message: "unknown key '\(keyName)'"))
                }
            } else {
                diagnostics.append(Diagnostic(line: lineNumber, message: "expected 'regex = <pattern>' or 'format = <template>'"))
            }
        }
        if let prev = currentPattern {
            appendRule(pattern: prev.pattern, format: nil, line: prev.line, rules: &rules, diagnostics: &diagnostics)
        }
        return (rules, diagnostics)
    }

    /// Load rules from `<dir>/link-rules.conf`, merging the always-on defaults first. A missing or unreadable
    /// file yields just the defaults with no diagnostics — non-blocking for first run.
    public static func load(configDirectory: URL) -> (rules: [Rule], diagnostics: [Diagnostic]) {
        let url = configDirectory.appendingPathComponent("link-rules.conf")
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            if FileManager.default.fileExists(atPath: url.path) {
                let d = Diagnostic(line: 0, message: "could not read link-rules.conf")
                return (defaultRules(), [d])
            }
            return (defaultRules(), [])
        }
        let parsed = parse(text)
        return (defaultRules() + parsed.rules, parsed.diagnostics)
    }

    /// Resolve the URL for a regex match: substitute `$N`/`$0` in `format` against the match's capture
    /// groups, or return the matched substring when no format is given. Missing groups expand to empty.
    public static func resolvedURL(match: NSTextCheckingResult, in text: String, format: String?, range: Range<String.Index>) -> String {
        guard let format else { return String(text[range]) }
        let nsText = text as NSString
        return expandFormat(format, match: match, in: nsText)
    }

    /// The first rule whose match at or straddling `offset` yields a URL, in rule order (defaults first).
    /// Nil when nothing matches — the caller reports "no link under cursor".
    public static func firstMatch(rules: [Rule], cellText: String, offset: Int) -> String? {
        guard offset >= 0, offset < cellText.utf16.count else { return nil }
        let nsText = cellText as NSString
        for rule in rules {
            let matches = rule.regex.matches(in: cellText, range: NSRange(location: 0, length: nsText.length))
            for match in matches where match.range.length > 0 {
                if match.range.contains(offset) || match.range.location == offset || match.range.upperBound == offset + 1 {
                    guard let range = Range(match.range, in: cellText) else { continue }
                    return resolvedURL(match: match, in: cellText, format: rule.format, range: range)
                }
            }
        }
        return nil
    }

    // MARK: - Private helpers

    /// Split a `key = value` config line into (key, value) with `=`, or nil for a line without one.
    private static func lineKeyValue(_ line: String) -> (String, String)? {
        guard let eq = line.firstIndex(of: "=") else { return nil }
        let key = String(line[..<eq]).trimmingCharacters(in: .whitespaces)
        let value = String(line[line.index(after: eq)...]).trimmingCharacters(in: .whitespaces)
        guard !key.isEmpty, !value.isEmpty else { return nil }
        return (key, value)
    }

    /// Compile a regex and append the rule (or a diagnostic when the pattern is malformed).
    private static func appendRule(pattern: String, format: String?, line: Int,
                                   rules: inout [Rule], diagnostics: inout [Diagnostic]) {
        guard let regex = try? NSRegularExpression(pattern: pattern) else {
            diagnostics.append(Diagnostic(line: line, message: "invalid regex '\(pattern)'"))
            return
        }
        rules.append(Rule(regex: regex, format: format, sourceLine: line))
    }

    /// Expand a `format` template over an `NSTextCheckingResult`; `$0` is the whole match, `$N` group N.
    private static func expandFormat(_ format: String, match: NSTextCheckingResult, in text: NSString) -> String {
        var out = ""
        var i = format.startIndex
        while i < format.endIndex {
            if format[i] == "$" {
                let next = format.index(after: i)
                if next < format.endIndex, format[next].isNumber {
                    var digits = ""
                    var d = next
                    while d < format.endIndex, format[d].isNumber {
                        digits.append(format[d])
                        d = format.index(after: d)
                    }
                    if let n = Int(digits), n < match.numberOfRanges {
                        let r = match.range(at: n)
                        out += r.location == NSNotFound ? "" : text.substring(with: r)
                    }
                    i = d
                    continue
                }
            }
            out.append(format[i])
            i = format.index(after: i)
        }
        return out
    }
}
