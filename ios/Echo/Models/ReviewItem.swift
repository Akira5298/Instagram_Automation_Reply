import Foundation

/// A draft ECHO held back for you (`GET /v1/reviews`): an automatic reply the
/// safety gate stopped, a "check with me first" friend, or a scheduled message.
struct ReviewItem: Identifiable, Codable, Hashable, Sendable {
    enum Origin: String, Codable, Sendable {
        case autoReply = "auto_reply"
        case scheduled
    }

    enum Action: String, Codable, Sendable {
        case send
        case dismiss
        case sentManually = "sent_manually"
    }

    let id: UUID
    let origin: Origin
    let friendId: UUID
    let friendName: String
    let reason: String
    let reasonText: String
    var draft: String?
    let inboundText: String?
    let createdAt: Date
    let windowExpiresAt: Date?

    var systemImage: String {
        switch reason {
        case "money": return "dollarsign.circle"
        case "commitment": return "signature"
        case "sensitive": return "lock.shield"
        case "emotional": return "heart.text.square"
        case "attachment": return "photo"
        case "review_first": return "checkmark.bubble"
        default: return "questionmark.bubble"
        }
    }

    /// Instagram only accepts replies until 24 h after their last message.
    func windowOpen(now: Date = .now) -> Bool {
        guard let windowExpiresAt else { return false }
        return windowExpiresAt > now
    }
}

/// Per-friend writing rules (`/v1/friends/{id}/style`), layered on your profile.
struct FriendStyle: Codable, Hashable, Sendable {
    var tone: String? = nil
    var formality: Int = 2
    var emojiLevel: Int = 2
    var maxSentences: Int? = nil
    var slangAllowed: Bool = true
    var customInstructions: String? = nil
    var avoidTopics: [String] = []

    static let levels = ["None", "Rare", "Some", "Often", "Lots"]
    static let formalityLevels = ["Very casual", "Casual", "Neutral", "Polite", "Formal"]
}

/// Account-wide automatic-reply settings (`/v1/automation`).
struct AutomationSettings: Codable, Hashable, Sendable {
    var paused: Bool
    var takeoverMinutes: Int
    var dailyReplyLimit: Int
    var consentRequestText: String
    var aiDisclosureText: String
}
