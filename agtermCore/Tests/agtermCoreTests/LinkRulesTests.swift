import Foundation
import Testing
@testable import agtermCore

struct LinkRulesTests {
    // MARK: parse (conf → rules)

    @Test func parsesRegexWithOptionalFormat() throws {
        let text = """
        # Startrek task keys
        regex = ([A-Z]+(-){1}[0-9]+)
        format = https://st.yandex-team.ru/$1

        regex = https?://[^[:space:]]+
        """
        let parsed = LinkRules.parse(text)
        #expect(parsed.diagnostics.isEmpty)
        #expect(parsed.rules.count == 2)
        #expect(parsed.rules[0].format == "https://st.yandex-team.ru/$1")
        #expect(parsed.rules[1].format == nil)
    }

    @Test func malformedRegexIsDiagnosticNotARule() {
        let text = "regex = ([unclosed\n"
        let parsed = LinkRules.parse(text)
        #expect(parsed.rules.isEmpty)
        #expect(parsed.diagnostics.count == 1)
    }

    @Test func formatWithoutRegexIsDiagnostic() {
        let text = "format = https://x/$1\n"
        let parsed = LinkRules.parse(text)
        #expect(parsed.rules.isEmpty)
        #expect(parsed.diagnostics.count == 1)
    }

    @Test func unknownKeyIsDiagnostic() {
        let text = "foo = bar\n"
        let parsed = LinkRules.parse(text)
        #expect(parsed.rules.isEmpty)
        #expect(parsed.diagnostics.count == 1)
    }

    @Test func blankAndCommentLinesIgnored() {
        let text = """
        # comment

        regex = http://[^[:space:]]+
        """
        let parsed = LinkRules.parse(text)
        #expect(parsed.rules.count == 1)
        #expect(parsed.diagnostics.isEmpty)
    }

    @Test func missingFileYieldsOnlyDefaults() {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("agt-linkrules-missing", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let loaded = LinkRules.load(configDirectory: dir)
        #expect(loaded.rules.count == LinkRules.defaultRules().count)
        #expect(loaded.diagnostics.isEmpty)
    }

    // MARK: resolvedURL (format substitution)

    @Test func formatSubstitutesCaptureGroups() throws {
        let rule = try #require(try NSRegularExpression(pattern: "([A-Z]+(-){1}[0-9]+)"))
        let text = "NOCDEV-1234"
        let ns = text as NSString
        let match = try #require(rule.firstMatch(in: text, range: NSRange(location: 0, length: ns.length)))
        let range = try #require(Range(match.range, in: text))
        let url = LinkRules.resolvedURL(match: match, in: text, format: "https://st.yandex-team.ru/$1", range: range)
        #expect(url == "https://st.yandex-team.ru/NOCDEV-1234")
    }

    @Test func noFormatReturnsMatchedSubstring() throws {
        let rule = try #require(try NSRegularExpression(pattern: "https?://[^[:space:]]+"))
        let text = "see https://example.com/x now"
        let ns = text as NSString
        let match = try #require(rule.firstMatch(in: text, range: NSRange(location: 0, length: ns.length)))
        let range = try #require(Range(match.range, in: text))
        let url = LinkRules.resolvedURL(match: match, in: text, format: nil, range: range)
        #expect(url == "https://example.com/x")
    }

    // MARK: firstMatch (cursor-cell → URL)

    @Test func findsMatchContainingOffset() throws {
        let rule = try #require(try NSRegularExpression(pattern: "[A-Z]+(-){1}[0-9]+"))
        let url = LinkRules.firstMatch(rules: [LinkRules.Rule(regex: rule, format: nil, sourceLine: nil)],
                                       cellText: "prefix NOCDEV-1234 suffix", offset: 8)
        #expect(url == "NOCDEV-1234")
    }

    @Test func defaultsWinBeforeUserRules() throws {
        // the default http rule comes before a user rule matching a substring of it, so the cursor on the
        // URL resolves to the full URL, not the inner task key.
        let task = try #require(try NSRegularExpression(pattern: "[A-Z]+(-){1}[0-9]+"))
        let rules = LinkRules.defaultRules() + [LinkRules.Rule(regex: task, format: "https://st.yandex-team.ru/$1", sourceLine: nil)]
        let text = "https://example.com/NOCDEV-1234"
        let url = LinkRules.firstMatch(rules: rules, cellText: text, offset: 8)
        #expect(url == text)
    }

    @Test func noMatchReturnsNil() throws {
        let rule = try #require(try NSRegularExpression(pattern: "[A-Z]+(-){1}[0-9]+"))
        let url = LinkRules.firstMatch(rules: [LinkRules.Rule(regex: rule, format: nil, sourceLine: nil)],
                                       cellText: "just prose", offset: 2)
        #expect(url == nil)
    }

    @Test func offsetOutsideTextReturnsNil() throws {
        let rule = try #require(try NSRegularExpression(pattern: "[A-Z]+(-){1}[0-9]+"))
        let url = LinkRules.firstMatch(rules: [LinkRules.Rule(regex: rule, format: nil, sourceLine: nil)],
                                       cellText: "NOCDEV-1234", offset: 50)
        #expect(url == nil)
    }
}
