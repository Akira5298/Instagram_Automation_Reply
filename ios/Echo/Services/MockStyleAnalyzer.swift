import Foundation

/// MOCK — a deterministic, on-device stand-in for the style analysis Claude
/// performs in Phase 2. It is intentionally simple: it lets the whole
/// learn → profile → dashboard loop work offline and is fully unit-testable.
struct MockStyleAnalyzer {
    struct Analysis: Equatable {
        var language: String
        var updates: [MemoryUpdate]
        var statedInterests: [String]

        static func == (lhs: Analysis, rhs: Analysis) -> Bool {
            lhs.language == rhs.language
                && lhs.statedInterests == rhs.statedInterests
                && lhs.updates.map(\.key) == rhs.updates.map(\.key)
        }
    }

    private static let slang: Set<String> = ["lol", "lmao", "ngl", "tbh", "idk", "omg", "bruh", "fr", "btw", "rn", "imo", "smh"]
    private static let italianWords: Set<String> = ["ciao", "grazie", "sono", "come", "stai", "anche", "molto", "oggi", "piace", "bene", "domani"]
    private static let laughs = ["haha", "hehe", "lol", "lmao", "ahah", "www", "笑", "哈哈"]

    private static let interestPatterns: [NSRegularExpression] = [
        #"\bI\s+(?:really\s+)?(?:love|like|enjoy|am into|'m into)\s+([^.,!?\n]{2,40})"#,
        #"\b(?:mi piace|adoro)\s+([^.,!?\n]{2,40})"#,
        #"([^\s、。！？]{1,15})が(?:大)?好き"#,
        #"我(?:很|真的)?喜欢([^，。！？\s]{1,15})"#,
    ].compactMap { try? NSRegularExpression(pattern: $0, options: [.caseInsensitive]) }

    func analyze(_ rawText: String) -> Analysis {
        let text = rawText.trimmingCharacters(in: .whitespacesAndNewlines)
        let language = Self.detectLanguage(text)
        let lower = text.lowercased()
        let words = lower
            .split(whereSeparator: { !$0.isLetter && $0 != "'" })
            .map(String.init)
        var updates: [MemoryUpdate] = []

        // Languages
        updates.append(MemoryUpdate(
            category: .languages,
            key: "language.\(language)",
            summary: "Writes in \(Self.languageName(language))",
            kind: .observation,
            confidence: 0.6
        ))

        // Slang
        for term in Set(words).intersection(Self.slang).sorted() {
            updates.append(MemoryUpdate(
                category: .slang,
                key: "slang.\(term)",
                summary: "Uses “\(term)”",
                kind: .observation,
                confidence: 0.45
            ))
        }

        // Emoji
        let emoji = text.unicodeScalars.filter { $0.properties.isEmojiPresentation }
        if !emoji.isEmpty {
            var sample = ""
            sample.unicodeScalars.append(contentsOf: emoji.prefix(3))
            updates.append(MemoryUpdate(
                category: .emoji,
                key: "emoji.uses",
                summary: "Uses emoji, e.g. \(sample)",
                kind: .observation,
                confidence: 0.45
            ))
        }

        // Sentence length
        let isCJK = language == "ja" || language == "zh-Hans"
        let isShort = isCJK ? text.count <= 14 : (words.count > 0 && words.count <= 6)
        if isShort {
            updates.append(MemoryUpdate(
                category: .sentenceStyle,
                key: "length.short",
                summary: "Tends to write short messages",
                kind: .observation,
                confidence: 0.35
            ))
        }

        // Punctuation
        if text.contains("!!") || text.contains("！！") {
            updates.append(MemoryUpdate(
                category: .sentenceStyle,
                key: "punctuation.exclaim",
                summary: "Uses expressive punctuation (!!)",
                kind: .observation,
                confidence: 0.4
            ))
        }
        if !isCJK, let first = text.first, first.isLowercase, !(text.last?.isPunctuation ?? false) {
            updates.append(MemoryUpdate(
                category: .sentenceStyle,
                key: "case.lowercase",
                summary: "Often skips capitals and full stops",
                kind: .observation,
                confidence: 0.35
            ))
        }

        // Humour
        if let laugh = Self.laughs.first(where: { lower.contains($0) }) {
            updates.append(MemoryUpdate(
                category: .humour,
                key: "humour.laugh.\(laugh)",
                summary: "Laughs in text (“\(laugh)”)",
                kind: .observation,
                confidence: 0.4
            ))
        }

        // Explicitly stated interests → facts, not guesses.
        let interests = Self.statedInterests(in: text)
        for interest in interests {
            updates.append(MemoryUpdate(
                category: .interests,
                key: "interest.\(interest.lowercased())",
                summary: interest,
                kind: .statedFact,
                confidence: 1
            ))
        }

        return Analysis(language: language, updates: updates, statedInterests: interests)
    }

    // MARK: - Helpers

    static func detectLanguage(_ text: String) -> String {
        var hasKana = false
        var hasHan = false
        for scalar in text.unicodeScalars {
            switch scalar.value {
            case 0x3040...0x30FF: hasKana = true
            case 0x4E00...0x9FFF: hasHan = true
            default: break
            }
        }
        if hasKana { return "ja" }
        if hasHan { return "zh-Hans" }
        let words = Set(text.lowercased().split(whereSeparator: { !$0.isLetter }).map(String.init))
        if !words.isDisjoint(with: italianWords) { return "it" }
        return "en"
    }

    static func languageName(_ code: String) -> String {
        switch code {
        case "ja": return "Japanese"
        case "zh-Hans": return "Mandarin (Simplified)"
        case "it": return "Italian"
        default: return "English"
        }
    }

    static func statedInterests(in text: String) -> [String] {
        let range = NSRange(text.startIndex..., in: text)
        var results: [String] = []
        for pattern in interestPatterns {
            for match in pattern.matches(in: text, range: range) where match.numberOfRanges > 1 {
                guard let captured = Range(match.range(at: 1), in: text) else { continue }
                // Drop trailing filler ("bouldering lol" → "bouldering").
                var words = text[captured].split(separator: " ").map(String.init)
                while let last = words.last, slang.contains(last.lowercased()) || laughs.contains(last.lowercased()) {
                    words.removeLast()
                }
                let value = words.joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
                if !value.isEmpty, !results.contains(value) {
                    results.append(value)
                }
            }
        }
        return results
    }
}
