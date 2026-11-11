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
    case active, off, needsAttention, paused

    var title: String {
        switch self {
        case .active: return "Auto"
        case .off: return "Manual"
        case .needsAttention: return "Review"
        case .paused: return "Paused"
        }
    }

    var systemImage: String {
        switch self {
        case .active: return "bolt.fill"
        case .off: return "hand.point.up.left.fill"
        case .needsAttention: return "exclamationmark.bubble.fill"
        case .paused: return "pause.circle.fill"
        }
    }

    var tint: Color {
        switch self {
        case .active: return EchoColor.cyan
        case .off: return EchoColor.textSecondary
        case .needsAttention: return EchoColor.warning
        case .paused: return EchoColor.danger
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

    var lastMessage: InstagramMessage? { messages.max { $0.sentAt < $1.sentAt } }

    func automationState(globalPause: Bool) -> AutomationState {
        if globalPause && friend.aiReplyEnabled { return .paused }
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
