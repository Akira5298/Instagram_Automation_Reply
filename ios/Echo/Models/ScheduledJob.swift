import SwiftUI

/// One scheduled message, shown as its next (or latest) occurrence.
/// Mirrors the backend's `GET /v1/jobs` shape (`scheduled_jobs` + latest `job_runs`).
struct ScheduledJob: Identifiable, Codable, Hashable, Sendable {
    enum Status: String, Codable, CaseIterable, Sendable {
        case scheduled
        case running
        case needsAttention = "needs_attention"
        case sending
        case sent
        case blocked
        case failed
        case retryScheduled = "retry_scheduled"
        case paused
        case cancelled

        init(from decoder: Decoder) throws {
            let raw = try decoder.singleValueContainer().decode(String.self)
            self = Status(rawValue: raw) ?? .scheduled
        }

        var title: String {
            switch self {
            case .scheduled: return "Scheduled"
            case .running: return "Preparing"
            case .needsAttention: return "Review"
            case .sending: return "Sending"
            case .sent: return "Sent"
            case .blocked: return "Blocked"
            case .failed: return "Failed"
            case .retryScheduled: return "Retrying"
            case .paused: return "Paused"
            case .cancelled: return "Cancelled"
            }
        }

        var systemImage: String {
            switch self {
            case .scheduled: return "clock.fill"
            case .running, .sending, .retryScheduled: return "arrow.triangle.2.circlepath"
            case .needsAttention: return "exclamationmark.bubble.fill"
            case .sent: return "checkmark.circle.fill"
            case .blocked: return "hand.raised.fill"
            case .failed: return "exclamationmark.triangle.fill"
            case .paused: return "pause.circle.fill"
            case .cancelled: return "xmark.circle.fill"
            }
        }

        var tint: Color {
            switch self {
            case .scheduled: return EchoColor.electricBlue
            case .running, .sending, .retryScheduled: return EchoColor.cyan
            case .needsAttention: return EchoColor.warning
            case .sent: return EchoColor.success
            case .blocked: return EchoColor.blocked
            case .failed: return EchoColor.danger
            case .paused, .cancelled: return EchoColor.textTertiary
            }
        }

        var isUpcoming: Bool { [.scheduled, .running, .sending, .retryScheduled, .paused].contains(self) }
        var isIssue: Bool { [.blocked, .failed, .needsAttention].contains(self) }
    }

    /// Result of the backend's deterministic pre-send check. Being scheduled is
    /// not the same as being allowed to send: this is only known at dispatch.
    enum Eligibility: String, Codable, Sendable {
        case checkedAtDispatch = "checked_at_dispatch"
        case eligible
        case outsideMessagingWindow = "outside_messaging_window"
        case noConsent = "no_consent"
        case automationPaused = "automation_paused"
        case missed
        case notPermitted = "not_permitted"

        init(from decoder: Decoder) throws {
            let raw = try decoder.singleValueContainer().decode(String.self)
            self = Eligibility(rawValue: raw) ?? .checkedAtDispatch
        }

        var explanation: String {
            switch self {
            case .checkedAtDispatch: return "Eligibility is checked right before sending"
            case .eligible: return "Passed all checks"
            case .outsideMessagingWindow: return "No message from them in the last 24h — Instagram doesn't allow sending"
            case .noConsent: return "They haven't agreed to AI-assisted messages"
            case .automationPaused: return "Automation was paused or is off for this friend"
            case .missed: return "Skipped because the server was offline at the scheduled time"
            case .notPermitted: return "Instagram isn't connected"
            }
        }
    }

    enum Recurrence: String, Codable, CaseIterable, Identifiable, Sendable {
        case once, daily, weekly, weekdays, custom

        init(from decoder: Decoder) throws {
            let raw = try decoder.singleValueContainer().decode(String.self)
            self = Recurrence(rawValue: raw) ?? .custom
        }

        static var choices: [Recurrence] { [.once, .daily, .weekly, .weekdays] }

        var id: String { rawValue }

        var title: String {
            switch self {
            case .once: return "One time"
            case .daily: return "Every day"
            case .weekly: return "Every week"
            case .weekdays: return "Weekdays"
            case .custom: return "Custom"
            }
        }

        /// RFC 5545 rule stored on the backend.
        var rrule: String? {
            switch self {
            case .once, .custom: return nil
            case .daily: return "FREQ=DAILY"
            case .weekly: return "FREQ=WEEKLY"
            case .weekdays: return "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
            }
        }
    }

    let id: UUID
    var friendID: UUID?
    var recipientIGSID: String
    var recipientName: String
    var scheduledAt: Date
    var timeZoneID: String
    var recurrence: Recurrence
    var instruction: String
    var style: Friend.StylePreset
    var automationEnabled: Bool
    var consentRecorded: Bool
    var eligibility: Eligibility
    var status: Status
    var lastExecutedAt: Date?
    var failureReason: String?
    var lastRunStatus: Status? = nil
    var latestRunID: UUID? = nil
    var draftText: String? = nil
    var createdVia: String? = nil
    /// Set when you wrote or picked the exact words: sent verbatim, no AI draft.
    var messageText: String? = nil

    /// Keys as produced by `convertFromSnakeCase` (e.g. "time_zone_id" → "timeZoneId").
    enum CodingKeys: String, CodingKey {
        case id
        case friendID = "friendId"
        case recipientIGSID = "recipientIgsid"
        case recipientName
        case scheduledAt
        case timeZoneID = "timeZoneId"
        case recurrence
        case instruction
        case style
        case automationEnabled
        case consentRecorded
        case eligibility
        case status
        case lastExecutedAt
        case failureReason
        case lastRunStatus
        case latestRunID = "latestRunId"
        case draftText
        case createdVia
        case messageText
    }

    var timeZone: TimeZone { TimeZone(identifier: timeZoneID) ?? .current }

    /// Formatted in the job's own timezone, not the phone's.
    var formattedTime: String {
        var style = Date.FormatStyle(date: .omitted, time: .shortened)
        style.timeZone = timeZone
        return scheduledAt.formatted(style)
    }

    var formattedDate: String {
        var style = Date.FormatStyle.dateTime.weekday(.abbreviated).day().month(.abbreviated)
        style.timeZone = timeZone
        return scheduledAt.formatted(style)
    }

    /// The status to show for the most recent attempt when the job itself is
    /// waiting for its next occurrence (e.g. "Scheduled · last: Blocked").
    var lastResultNote: Status? {
        guard let lastRunStatus, lastRunStatus != status, status == .scheduled else { return nil }
        return lastRunStatus
    }
}

/// One attempt/occurrence of a scheduled job (`GET /v1/jobs/{id}/runs`).
struct JobRun: Identifiable, Codable, Hashable, Sendable {
    let id: UUID
    var scheduledFor: Date
    var status: ScheduledJob.Status
    var attempt: Int
    var executedAt: Date?
    var draftText: String?
    var safetyFlags: [String]
    var blockedReason: String?
    var failureReason: String?
    var approvedByUser: Bool

    enum Action: String, Codable, Sendable {
        case approve
        case dismiss
        case sentManually = "sent_manually"
    }

    var reasonText: String? {
        if let failureReason { return failureReason.replacingOccurrences(of: "_", with: " ") }
        guard let blockedReason else { return nil }
        return blockedReason
            .split(separator: ",")
            .map { $0.replacingOccurrences(of: "_", with: " ") }
            .joined(separator: ", ")
    }
}

/// Input for creating a scheduled message.
struct NewJobDraft: Sendable {
    var friend: Friend
    var scheduledAt: Date
    var timeZoneID: String
    var recurrence: ScheduledJob.Recurrence
    var instruction: String
    var automationEnabled: Bool
    /// Exact words to send instead of an instruction for ECHO.
    var messageText: String? = nil
}
