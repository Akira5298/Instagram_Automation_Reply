import Foundation

struct NotificationPreferences: Codable, Hashable, Sendable {
    var messageSent = true
    var jobBlocked = true
    var deliveryFailed = true
    var needsAttention = true
    var automationChanged = true
    var connectionError = true
    var scheduleReminder = false
    // Phase 6: quiet hours (minutes after local midnight, in your scheduling timezone).
    var quietHoursEnabled = false
    var quietStartMinutes = 23 * 60
    var quietEndMinutes = 7 * 60
    var holdRepliesInQuietHours = false

    init() {}

    /// Tolerates settings saved by older versions (missing keys keep their defaults),
    /// so updating the app never resets your backend URL or other settings.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let d = NotificationPreferences()
        messageSent = try c.decodeIfPresent(Bool.self, forKey: .messageSent) ?? d.messageSent
        jobBlocked = try c.decodeIfPresent(Bool.self, forKey: .jobBlocked) ?? d.jobBlocked
        deliveryFailed = try c.decodeIfPresent(Bool.self, forKey: .deliveryFailed) ?? d.deliveryFailed
        needsAttention = try c.decodeIfPresent(Bool.self, forKey: .needsAttention) ?? d.needsAttention
        automationChanged = try c.decodeIfPresent(Bool.self, forKey: .automationChanged) ?? d.automationChanged
        connectionError = try c.decodeIfPresent(Bool.self, forKey: .connectionError) ?? d.connectionError
        scheduleReminder = try c.decodeIfPresent(Bool.self, forKey: .scheduleReminder) ?? d.scheduleReminder
        quietHoursEnabled = try c.decodeIfPresent(Bool.self, forKey: .quietHoursEnabled) ?? d.quietHoursEnabled
        quietStartMinutes = try c.decodeIfPresent(Int.self, forKey: .quietStartMinutes) ?? d.quietStartMinutes
        quietEndMinutes = try c.decodeIfPresent(Int.self, forKey: .quietEndMinutes) ?? d.quietEndMinutes
        holdRepliesInQuietHours = try c.decodeIfPresent(Bool.self, forKey: .holdRepliesInQuietHours) ?? d.holdRepliesInQuietHours
    }
}

/// `/v1/notifications/preferences` wire format (times as "HH:MM:SS").
struct ServerNotificationPreferences: Codable, Sendable {
    var messageSent: Bool
    var jobBlocked: Bool
    var deliveryFailed: Bool
    var needsAttention: Bool
    var automationChanged: Bool
    var connectionError: Bool
    var scheduleReminder: Bool
    var quietHoursStart: String?
    var quietHoursEnd: String?
    var quietHoursHoldReplies: Bool

    init(_ p: NotificationPreferences) {
        messageSent = p.messageSent
        jobBlocked = p.jobBlocked
        deliveryFailed = p.deliveryFailed
        needsAttention = p.needsAttention
        automationChanged = p.automationChanged
        connectionError = p.connectionError
        scheduleReminder = p.scheduleReminder
        quietHoursStart = p.quietHoursEnabled ? Self.clock(p.quietStartMinutes) : nil
        quietHoursEnd = p.quietHoursEnabled ? Self.clock(p.quietEndMinutes) : nil
        quietHoursHoldReplies = p.holdRepliesInQuietHours
    }

    var local: NotificationPreferences {
        var p = NotificationPreferences()
        p.messageSent = messageSent
        p.jobBlocked = jobBlocked
        p.deliveryFailed = deliveryFailed
        p.needsAttention = needsAttention
        p.automationChanged = automationChanged
        p.connectionError = connectionError
        p.scheduleReminder = scheduleReminder
        if let start = quietHoursStart.flatMap(Self.minutes), let end = quietHoursEnd.flatMap(Self.minutes) {
            p.quietHoursEnabled = true
            p.quietStartMinutes = start
            p.quietEndMinutes = end
        }
        p.holdRepliesInQuietHours = quietHoursHoldReplies
        return p
    }

    static func clock(_ minutes: Int) -> String {
        String(format: "%02d:%02d:00", (minutes / 60) % 24, minutes % 60)
    }

    static func minutes(_ text: String) -> Int? {
        let parts = text.split(separator: ":").compactMap { Int($0) }
        guard parts.count >= 2 else { return nil }
        return parts[0] * 60 + parts[1]
    }
}

struct AppSettings: Codable, Hashable, Sendable {
    /// Global emergency stop. Phase 1 stores it on the device; from Phase 3
    /// the backend flag (`app_users.automation_paused`) is authoritative and
    /// the worker checks it before every send.
    var automationPaused = false
    var automationPausedAt: Date?
    var notifications = NotificationPreferences()
    var backendBaseURL = "http://localhost:8000"
    var useMockServices = true
    var defaultTimeZoneID = "Asia/Kuala_Lumpur"
    var messageRetentionDays = 30
}
