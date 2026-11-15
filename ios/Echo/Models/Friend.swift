import SwiftUI

/// Raw values match the backend JSON (snake_case values).
struct Friend: Identifiable, Codable, Hashable, Sendable {
    enum StylePreset: String, Codable, CaseIterable, Identifiable, Sendable {
        case closeFriend = "close_friend"
        case classmate
        case professional
        case custom

        var id: String { rawValue }

        var title: String {
            switch self {
            case .closeFriend: return "Close friend"
            case .classmate: return "Classmate"
            case .professional: return "Professional"
            case .custom: return "Custom"
            }
        }

        var summary: String {
            switch self {
            case .closeFriend: return "Casual, funny, short, natural emoji"
            case .classmate: return "Friendly, clear, moderately casual"
            case .professional: return "Polite, more formal, no slang"
            case .custom: return "Your own rules"
            }
        }
    }

    enum ConsentStatus: String, Codable, CaseIterable, Sendable {
        case unknown, requested, granted, revoked

        var title: String {
            switch self {
            case .unknown: return "Not asked"
            case .requested: return "Requested"
            case .granted: return "Granted"
            case .revoked: return "Opted out"
            }
        }

        var tint: Color {
            switch self {
            case .unknown: return EchoColor.textTertiary
            case .requested: return EchoColor.warning
            case .granted: return EchoColor.success
            case .revoked: return EchoColor.danger
            }
        }
    }

    let id: UUID
    /// Instagram-scoped ID — only known once they've messaged you.
    var igsid: String
    var displayName: String
    var username: String
    var style: StylePreset
    var language: String
    var aiReplyEnabled: Bool
    var consent: ConsentStatus
    var lastInboundAt: Date?
    var manualOverrideUntil: Date? = nil
    /// How long ECHO waits before replying, so a burst of messages gets one answer (backend default 45 s).
    var replyDelaySeconds: Int? = nil
    /// Every auto reply waits in the review queue for your OK.
    var reviewFirst: Bool? = nil
    var dailyReplyLimit: Int? = nil

    /// You're handling this chat yourself; ECHO stays quiet until then.
    func isTakenOver(now: Date = .now) -> Bool {
        guard let manualOverrideUntil else { return false }
        return manualOverrideUntil > now
    }

    /// Display-only estimate. The backend's deterministic check is the
    /// source of truth before anything is sent.
    func standardWindowRemaining(now: Date = .now) -> TimeInterval? {
        guard let lastInboundAt else { return nil }
        let remaining = lastInboundAt.addingTimeInterval(24 * 60 * 60).timeIntervalSince(now)
        return remaining > 0 ? remaining : nil
    }

    var firstName: String { displayName.components(separatedBy: " ").first ?? displayName }
}

enum AutomationState: String, Codable, Sendable {
    case active, off, needsAttention, paused, takenOver

    var title: String {
        switch self {
        case .active: return "Auto"
        case .off: return "Manual"
        case .needsAttention: return "Review"
        case .paused: return "Paused"
        case .takenOver: return "You"
        }
    }

    var systemImage: String {
        switch self {
        case .active: return "bolt.fill"
        case .off: return "hand.point.up.left.fill"
        case .needsAttention: return "exclamationmark.bubble.fill"
        case .paused: return "pause.circle.fill"
        case .takenOver: return "person.fill.checkmark"
        }
    }

    var tint: Color {
        switch self {
        case .active: return EchoColor.cyan
        case .off: return EchoColor.textSecondary
        case .needsAttention: return EchoColor.warning
        case .paused: return EchoColor.danger
        case .takenOver: return EchoColor.blocked
        }
    }
}

struct InstagramMessage: Identifiable, Codable, Hashable, Sendable {
    enum Sender: String, Codable, Sendable {
        case friend
        case me
        case echoAuto = "echo_auto"
        case echoScheduled = "echo_scheduled"
    }

    let id: UUID
    let sender: Sender
    let text: String
    let sentAt: Date

    init(id: UUID = UUID(), sender: Sender, text: String, sentAt: Date) {
        self.id = id
        self.sender = sender
        self.text = text
        self.sentAt = sentAt
    }
}

struct ConversationSummary: Identifiable, Codable, Hashable, Sendable {
    let id: UUID
    var friend: Friend
    var messages: [InstagramMessage]
    var unreadCount: Int
    /// What happened to the latest message ECHO considered answering (server only).
    var lastAutoReply: AutoReplyOutcome? = nil

    var lastMessage: InstagramMessage? { messages.max { $0.sentAt < $1.sentAt } }

    func automationState(globalPause: Bool, pendingReviews: Int = 0) -> AutomationState {
        if globalPause && friend.aiReplyEnabled { return .paused }
        if pendingReviews > 0 { return .needsAttention }
        if friend.aiReplyEnabled && friend.isTakenOver() { return .takenOver }
        if unreadCount > 0 && friend.aiReplyEnabled && friend.consent != .granted { return .needsAttention }
        return friend.aiReplyEnabled ? .active : .off
    }
}

/// An entry in the activity feed (sent / blocked / failed / needs attention …).
struct ActivityItem: Identifiable, Codable, Hashable, Sendable {
    let id: UUID
    let category: String
    let title: String
    let body: String
    let createdAt: Date
    var readAt: Date?

    var systemImage: String {
        switch category {
        case "message_sent": return "checkmark.circle.fill"
        case "job_blocked": return "hand.raised.fill"
        case "delivery_failed": return "exclamationmark.triangle.fill"
        case "needs_attention": return "exclamationmark.bubble.fill"
        case "automation_changed": return "pause.circle.fill"
        default: return "bell.fill"
        }
    }

    var tint: Color {
        switch category {
        case "message_sent": return EchoColor.success
        case "job_blocked": return EchoColor.blocked
        case "delivery_failed": return EchoColor.danger
        case "needs_attention": return EchoColor.warning
        default: return EchoColor.cyan
        }
    }
}

/// `GET /v1/instagram/status`
struct InstagramStatus: Codable, Hashable, Sendable {
    var connected: Bool
    var configured: Bool
    var provider: String
    var mockAccount: Bool
    var username: String?
    var accountType: String?
    var status: String?
    var tokenExpiresAt: Date?
    var webhooksSubscribed: Bool?
    var lastWebhookAt: Date?
    var lastSyncedAt: Date?
    var lastError: String?
    /// "unified" (Instagram API with Unified Login) or "instagram" (legacy Instagram Login).
    var loginMode: String?
    /// The connected account was set up with a different login than the server now uses.
    var needsReconnect: Bool?
    /// .env keys the server still needs before Instagram can be connected.
    var missingSettings: [String]?

    var usesMockInstagram: Bool { provider == "mock" }

    var accountTypeTitle: String? {
        switch accountType {
        case "MEDIA_CREATOR": return "Creator"
        case "BUSINESS": return "Business"
        default: return accountType?.capitalized
        }
    }
}


/// Server's plain-words account of the latest automatic reply for a chat,
/// e.g. "No reply: AI replies are off for this friend."
struct AutoReplyOutcome: Codable, Hashable, Sendable {
    let status: String
    let reason: String?
    let text: String
    let at: Date

    var systemImage: String {
        switch status {
        case "sent": return "checkmark.circle.fill"
        case "pending", "running", "sending": return "clock"
        case "needs_attention": return "exclamationmark.bubble.fill"
        case "blocked", "failed": return "xmark.octagon"
        default: return "minus.circle"
        }
    }

    var tint: Color {
        switch status {
        case "sent": return EchoColor.success
        case "pending", "running", "sending": return EchoColor.cyan
        case "needs_attention": return EchoColor.warning
        case "blocked", "failed": return EchoColor.danger
        default: return EchoColor.textTertiary
        }
    }
}

/// `GET /v1/updates` long-poll answer.
struct LiveUpdate: Decodable, Sendable {
    let cursor: String
    let changed: Bool
}
