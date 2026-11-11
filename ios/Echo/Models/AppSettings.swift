import Foundation

struct NotificationPreferences: Codable, Hashable, Sendable {
    var messageSent = true
    var jobBlocked = true
    var deliveryFailed = true
    var needsAttention = true
    var automationChanged = true
    var connectionError = true
    var scheduleReminder = false
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
