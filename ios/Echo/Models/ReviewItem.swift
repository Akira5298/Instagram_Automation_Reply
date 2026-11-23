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
    /// Tap-to-send options ECHO wrote in your voice (the draft first). Optional so
    /// older servers still decode.
    var suggestions: [Suggestion]? = nil

    /// One tap-to-send option: words, or a ❤️ reaction on their message.
    struct Suggestion: Codable, Hashable, Sendable, Identifiable {
        enum Kind: String, Codable, Sendable { case text, reaction }
        let text: String
        let kind: Kind
        var id: String { "\(kind.rawValue):\(text)" }
    }

    var options: [Suggestion] { suggestions ?? [] }

    var systemImage: String {
        switch reason {
        case "money": return "dollarsign.circle"
        case "commitment": return "signature"
        case "sensitive": return "lock.shield"
        case "emotional": return "heart.text.square"
        case "attachment": return "photo"
        case "review_first", "review_mode": return "checkmark.bubble"
        case "personal_info": return "location.slash"
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
    /// What ECHO learned from the messages you wrote to this friend yourself
    /// (read-only; your rules above always win).
    var learned: LearnedStyle? = nil
    var learnedAt: Date? = nil
    var learnedMessageCount: Int? = nil

    static let levels = ["None", "Rare", "Some", "Often", "Lots"]
    static let formalityLevels = ["Very casual", "Casual", "Neutral", "Polite", "Formal"]
}

/// How you write to one friend, learned from your own messages.
struct LearnedStyle: Codable, Hashable, Sendable {
    var tone: String? = nil
    var formality: Int? = nil
    var emojiLevel: Int? = nil
    var typicalLength: String? = nil
    var favouriteEmoji: [String]? = nil
    var phrases: [String]? = nil
    var humor: String? = nil
    var language: String? = nil
    var notes: String? = nil

    var isEmpty: Bool { (tone ?? "").isEmpty && (phrases ?? []).isEmpty && (favouriteEmoji ?? []).isEmpty }
}

/// Account-wide automatic-reply settings (`/v1/automation`).
struct AutomationSettings: Codable, Hashable, Sendable {
    enum ReplyMode: String, Codable, CaseIterable, Identifiable, Sendable {
        case auto, review
        var id: String { rawValue }
        var title: String { self == .auto ? "Fully automatic" : "Review every reply" }
    }

    enum ReplyLength: String, Codable, CaseIterable, Identifiable, Sendable {
        case short, medium, long, adaptive
        var id: String { rawValue }
        var title: String { rawValue.capitalized }
        var summary: String {
            switch self {
            case .short: return "A few words"
            case .medium: return "One or two sentences"
            case .long: return "Up to a short paragraph"
            case .adaptive: return "Matches them and how you usually write"
            }
        }
    }

    var paused: Bool
    var takeoverMinutes: Int
    var dailyReplyLimit: Int
    var consentRequestText: String
    var aiDisclosureText: String
    // Optional so an older server (without these) still decodes.
    var autoReplyMode: ReplyMode? = nil
    var replyLength: ReplyLength? = nil
    var learnFromChats: Bool? = nil
}

/// A message idea for the scheduler (`/v1/friends/{id}/message-suggestions`).
struct MessageIdea: Codable, Hashable, Sendable, Identifiable {
    let text: String
    let vibe: String
    var id: String { text }
}

/// Something ECHO remembers about a friend (`/v1/friends/{id}/memories`).
/// Only approved, unexpired memories are used when ECHO writes to them.
struct FriendMemory: Identifiable, Codable, Hashable, Sendable {
    enum Sensitivity: String, Codable, Sendable { case normal, sensitive }

    let id: UUID
    var content: String
    var source: String
    var sensitivity: Sensitivity
    var approved: Bool
    var expiresAt: Date?
    let createdAt: Date
}
