import Foundation

/// MOCK chat backend. Same interface the real backend client will use, so the
/// chat screen is fully testable without a server or API key.
@MainActor
final class MockChatService: ChatService {
    private let analyzer = MockStyleAnalyzer()
    private let simulateLatency: Bool

    init(simulateLatency: Bool = true) {
        self.simulateLatency = simulateLatency
    }

    var isRemote: Bool { false }

    func history() async throws -> [ChatMessage]? { nil }

    func clearHistory() async throws {}

    func reply(to message: ChatMessage, history: [ChatMessage]) async throws -> ChatMessage {
        let trimmed = message.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw ChatServiceError.emptyMessage }

        if simulateLatency {
            let millis = min(1_600, 650 + trimmed.count * 8)
            try await Task.sleep(for: .milliseconds(millis))
        }

        let analysis = analyzer.analyze(trimmed)
        let turnIndex = history.filter { $0.role == .user }.count
        let replyText = Self.compose(language: analysis.language, interests: analysis.statedInterests, turn: turnIndex)

        return ChatMessage(
            role: .assistant,
            text: replyText,
            language: analysis.language,
            memoryUpdates: analysis.updates
        )
    }

    // MARK: - Canned, multilingual replies

    static func compose(language: String, interests: [String], turn: Int) -> String {
        if let interest = interests.first {
            switch language {
            case "ja": return "\(interest)いいね！どうやってハマったの？"
            case "zh-Hans": return "\(interest)听起来很有意思！你是怎么开始喜欢的？"
            case "it": return "\(interest), che bello! Come ci sei arrivato/a?"
            default: return "Nice — \(interest) sounds fun. What got you into it?"
            }
        }
        let options: [String]
        switch language {
        case "ja":
            options = [
                "なるほど！仲のいい友達にはどんな感じで送る？",
                "いいね、もっと教えて！",
                "今日はどんな一日だった？",
            ]
        case "zh-Hans":
            options = [
                "明白了！你平时跟好朋友会怎么说这句话？",
                "有意思！再多说一点吧。",
                "你今天过得怎么样？",
            ]
        case "it":
            options = [
                "Capito! Come lo scriveresti a un amico stretto?",
                "Interessante! Raccontami di più.",
                "Com'è andata la giornata?",
            ]
        default:
            options = [
                "Got it. If you were texting a close friend about that, how would you say it?",
                "Interesting! Do you talk like that with everyone, or only certain people?",
                "Noted. What's been the best part of your day so far?",
                "Fair enough. Do you usually reply fast, or let messages sit for a bit?",
            ]
        }
        return options[turn % options.count]
    }
}
