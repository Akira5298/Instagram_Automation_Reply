import Foundation

/// Where friends, conversations, scheduled jobs and activity come from.
///  * `MockMessagingBackend` — sample data on the device (offline mode).
///  * `RemoteMessagingBackend` — your backend (`/v1/friends`, `/v1/jobs`, …),
///    whose worker actually executes the schedule.
@MainActor
protocol MessagingBackend {
    var isRemote: Bool { get }

    func loadConversations() async throws -> [ConversationSummary]
    func updateFriend(_ friend: Friend) async throws -> Friend
    func sendManualReply(_ text: String, to friend: Friend) async throws -> InstagramMessage
    /// Dev/testing: pretend the friend just messaged you (opens the 24h window).
    func simulateInbound(_ text: String, from friend: Friend) async throws
    func seedSampleData() async throws

    func loadJobs() async throws -> [ScheduledJob]
    func createJob(_ draft: NewJobDraft) async throws -> ScheduledJob
    func pause(_ job: ScheduledJob) async throws -> ScheduledJob
    func resume(_ job: ScheduledJob) async throws -> ScheduledJob
    func cancel(_ job: ScheduledJob) async throws
    func runNow(_ job: ScheduledJob) async throws
    func runs(for job: ScheduledJob) async throws -> [JobRun]
    func resolve(_ run: JobRun, action: JobRun.Action) async throws

    func loadActivity() async throws -> [ActivityItem]
    func markActivityRead() async throws
    /// nil when the backend doesn't hold the pause flag (mock mode).
    func loadAutomationPaused() async throws -> Bool?
    func setAutomationPaused(_ paused: Bool) async throws

    // Instagram connection (backend only)
    func loadInstagramStatus() async throws -> InstagramStatus?
    func instagramAuthorizeURL() async throws -> URL
    func syncInstagram() async throws -> Int
    func disconnectInstagram(deleteMessages: Bool) async throws
}

// MARK: - Remote

@MainActor
final class RemoteMessagingBackend: MessagingBackend {
    private let api: APIClient
    fileprivate var apiClient: APIClient { api }

    init(api: APIClient) {
        self.api = api
    }

    var isRemote: Bool { true }

    private struct ConversationsResponse: Decodable { let conversations: [ConversationSummary] }
    private struct JobsResponse: Decodable { let jobs: [ScheduledJob] }
    private struct RunsResponse: Decodable { let runs: [JobRun] }
    private struct ActivityResponse: Decodable { let notifications: [ActivityItem] }
    private struct AutomationResponse: Codable { let paused: Bool }
    private struct Ignored: Decodable {}

    private struct FriendPatch: Encodable {
        let aiReplyEnabled: Bool
        let style: Friend.StylePreset
        let language: String
        let consent: Friend.ConsentStatus
        let manualOverrideUntil: Date?
    }

    private struct TextBody: Encodable { let text: String }

    private struct JobCreate: Encodable {
        let friendId: UUID
        let scheduledAt: Date
        let timezone: String
        let recurrence: ScheduledJob.Recurrence
        let instruction: String
        let automationEnabled: Bool
    }

    private struct RunActionBody: Encodable { let action: JobRun.Action }

    private func id(_ uuid: UUID) -> String { uuid.uuidString.lowercased() }

    func loadConversations() async throws -> [ConversationSummary] {
        let response: ConversationsResponse = try await api.get("v1/conversations")
        return response.conversations
    }

    func updateFriend(_ friend: Friend) async throws -> Friend {
        try await api.patch("v1/friends/\(id(friend.id))", body: FriendPatch(
            aiReplyEnabled: friend.aiReplyEnabled,
            style: friend.style,
            language: friend.language,
            consent: friend.consent,
            manualOverrideUntil: friend.manualOverrideUntil
        ))
    }

    func sendManualReply(_ text: String, to friend: Friend) async throws -> InstagramMessage {
        try await api.post("v1/friends/\(id(friend.id))/messages", body: TextBody(text: text))
    }

    func simulateInbound(_ text: String, from friend: Friend) async throws {
        let _: Ignored = try await api.post("v1/dev/friends/\(id(friend.id))/inbound", body: TextBody(text: text))
    }

    func seedSampleData() async throws {
        _ = try await api.send("POST", "v1/dev/seed")
    }

    func loadJobs() async throws -> [ScheduledJob] {
        let response: JobsResponse = try await api.get("v1/jobs")
        return response.jobs
    }

    func createJob(_ draft: NewJobDraft) async throws -> ScheduledJob {
        try await api.post("v1/jobs", body: JobCreate(
            friendId: draft.friend.id,
            scheduledAt: draft.scheduledAt,
            timezone: draft.timeZoneID,
            recurrence: draft.recurrence,
            instruction: draft.instruction,
            automationEnabled: draft.automationEnabled
        ))
    }

    func pause(_ job: ScheduledJob) async throws -> ScheduledJob {
        try JSONDecoderBox.decode(try await api.send("POST", "v1/jobs/\(id(job.id))/pause"))
    }

    func resume(_ job: ScheduledJob) async throws -> ScheduledJob {
        try JSONDecoderBox.decode(try await api.send("POST", "v1/jobs/\(id(job.id))/resume"))
    }

    func cancel(_ job: ScheduledJob) async throws {
        try await api.delete("v1/jobs/\(id(job.id))")
    }

    func runNow(_ job: ScheduledJob) async throws {
        _ = try await api.send("POST", "v1/jobs/\(id(job.id))/run-now")
    }

    func runs(for job: ScheduledJob) async throws -> [JobRun] {
        let response: RunsResponse = try await api.get("v1/jobs/\(id(job.id))/runs")
        return response.runs
    }

    func resolve(_ run: JobRun, action: JobRun.Action) async throws {
        let _: Ignored = try await api.post("v1/jobs/runs/\(id(run.id))", body: RunActionBody(action: action))
    }

    func loadActivity() async throws -> [ActivityItem] {
        let response: ActivityResponse = try await api.get("v1/notifications")
        return response.notifications
    }

    func markActivityRead() async throws {
        _ = try await api.send("POST", "v1/notifications/read")
    }

    func loadAutomationPaused() async throws -> Bool? {
        let response: AutomationResponse = try await api.get("v1/automation")
        return response.paused
    }

    func setAutomationPaused(_ paused: Bool) async throws {
        let body = try APIClient.encoder.encode(AutomationResponse(paused: paused))
        _ = try await api.send("PUT", "v1/automation", body: body)
    }
}

extension RemoteMessagingBackend {
    private struct ConnectResponse: Decodable { let authorizeUrl: String }
    private struct SyncResponse: Decodable { let friends: Int; let messages: Int }
    private struct DisconnectBody: Encodable { let deleteMessages: Bool }
    private struct EmptyBody: Encodable {}

    func loadInstagramStatus() async throws -> InstagramStatus? {
        try await apiClient.get("v1/instagram/status")
    }

    func instagramAuthorizeURL() async throws -> URL {
        let response: ConnectResponse = try await apiClient.post("v1/instagram/connect", body: EmptyBody())
        guard let url = URL(string: response.authorizeUrl) else {
            throw APIError.decoding("bad authorize URL")
        }
        return url
    }

    func syncInstagram() async throws -> Int {
        let response: SyncResponse = try await apiClient.post("v1/instagram/sync", body: EmptyBody())
        return response.messages
    }

    func disconnectInstagram(deleteMessages: Bool) async throws {
        let _: InstagramStatus = try await apiClient.post("v1/instagram/disconnect",
                                                         body: DisconnectBody(deleteMessages: deleteMessages))
    }
}

/// Decodes with the API's conventions (snake_case keys, server dates).
enum JSONDecoderBox {
    static func decode<T: Decodable>(_ data: Data) throws -> T {
        do {
            return try APIClient.decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decoding(String(describing: error).prefix(160).description)
        }
    }
}

// MARK: - Mock (offline)

/// Sample data that behaves plausibly: jobs can be created, paused and
/// cancelled; "Run now" applies the same eligibility rules as the backend.
@MainActor
final class MockMessagingBackend: MessagingBackend {
    private(set) var conversations: [ConversationSummary]
    private(set) var jobs: [ScheduledJob]
    private var activity: [ActivityItem] = []
    private var runsByJob: [UUID: [JobRun]] = [:]
    private var paused = false

    init(now: Date = .now) {
        let sample = MockMessagingBackend.sample(now: now)
        conversations = sample.conversations
        jobs = sample.jobs
    }

    var isRemote: Bool { false }

    func loadConversations() async throws -> [ConversationSummary] { conversations }

    func updateFriend(_ friend: Friend) async throws -> Friend {
        if let index = conversations.firstIndex(where: { $0.friend.id == friend.id }) {
            conversations[index].friend = friend
        }
        return friend
    }

    func sendManualReply(_ text: String, to friend: Friend) async throws -> InstagramMessage {
        guard friend.standardWindowRemaining() != nil else {
            throw APIError.server(status: 409, message: "Instagram only allows replies within 24 hours of \(friend.displayName)'s last message.")
        }
        let message = InstagramMessage(sender: .me, text: text, sentAt: .now)
        append(message, to: friend.id)
        return message
    }

    func simulateInbound(_ text: String, from friend: Friend) async throws {
        append(InstagramMessage(sender: .friend, text: text, sentAt: .now), to: friend.id)
        if let index = conversations.firstIndex(where: { $0.friend.id == friend.id }) {
            conversations[index].friend.lastInboundAt = .now
        }
    }

    func seedSampleData() async throws {}

    func loadJobs() async throws -> [ScheduledJob] { jobs }

    func createJob(_ draft: NewJobDraft) async throws -> ScheduledJob {
        guard draft.scheduledAt > .now else {
            throw APIError.server(status: 422, message: "Pick a time in the future.")
        }
        let job = ScheduledJob(
            id: UUID(),
            friendID: draft.friend.id,
            recipientIGSID: draft.friend.igsid,
            recipientName: draft.friend.displayName,
            scheduledAt: draft.scheduledAt,
            timeZoneID: draft.timeZoneID,
            recurrence: draft.recurrence,
            instruction: draft.instruction.trimmingCharacters(in: .whitespacesAndNewlines),
            style: draft.friend.style,
            automationEnabled: draft.automationEnabled,
            consentRecorded: draft.friend.consent == .granted,
            eligibility: .checkedAtDispatch,
            status: .scheduled
        )
        jobs.append(job)
        return job
    }

    func pause(_ job: ScheduledJob) async throws -> ScheduledJob {
        try mutate(job.id) { $0.status = .paused }
    }

    func resume(_ job: ScheduledJob) async throws -> ScheduledJob {
        try mutate(job.id) { $0.status = .scheduled }
    }

    func cancel(_ job: ScheduledJob) async throws {
        _ = try mutate(job.id) { $0.status = .cancelled }
    }

    /// Mirrors the backend's gates so offline mode teaches the real rules.
    func runNow(_ job: ScheduledJob) async throws {
        guard let friend = conversations.first(where: { $0.friend.id == job.friendID || $0.friend.igsid == job.recipientIGSID })?.friend
        else { return }
        let draft = "[mock] \(job.instruction)"
        var run = JobRun(id: UUID(), scheduledFor: .now, status: .sent, attempt: 1, executedAt: .now,
                         draftText: draft, safetyFlags: [], blockedReason: nil, failureReason: nil, approvedByUser: false)
        var eligibility = ScheduledJob.Eligibility.eligible
        if paused {
            run.status = .blocked; run.blockedReason = "global_pause"; eligibility = .automationPaused
        } else if !job.automationEnabled {
            run.status = .needsAttention; run.blockedReason = "automation_off"
        } else if friend.consent != .granted {
            run.status = .blocked; run.blockedReason = "no_consent"; eligibility = .noConsent
        } else if friend.standardWindowRemaining() == nil {
            run.status = .blocked; run.blockedReason = "outside_standard_window"; eligibility = .outsideMessagingWindow
        } else {
            append(InstagramMessage(sender: .echoScheduled, text: draft, sentAt: .now), to: friend.id)
        }
        runsByJob[job.id, default: []].insert(run, at: 0)
        _ = try mutate(job.id) {
            $0.lastRunStatus = run.status
            $0.lastExecutedAt = .now
            $0.eligibility = eligibility
            $0.draftText = draft
            $0.latestRunID = run.id
            $0.failureReason = run.reasonText
            if run.status == .needsAttention { $0.status = .needsAttention }
        }
        let title: String
        switch run.status {
        case .sent: title = "Sent to \(friend.displayName)"
        case .needsAttention: title = "Review message to \(friend.displayName)"
        default: title = "Message to \(friend.displayName) blocked"
        }
        activity.insert(ActivityItem(id: UUID(), category: run.status == .sent ? "message_sent" : "job_blocked",
                                     title: title, body: run.reasonText ?? draft, createdAt: .now), at: 0)
    }

    func runs(for job: ScheduledJob) async throws -> [JobRun] { runsByJob[job.id] ?? [] }

    func resolve(_ run: JobRun, action: JobRun.Action) async throws {
        for (jobID, runs) in runsByJob {
            guard let index = runs.firstIndex(where: { $0.id == run.id }) else { continue }
            runsByJob[jobID]?[index].status = .cancelled
            runsByJob[jobID]?[index].blockedReason = action.rawValue
            _ = try mutate(jobID) { $0.status = .scheduled; $0.lastRunStatus = .cancelled }
        }
    }

    func loadActivity() async throws -> [ActivityItem] { activity }

    func markActivityRead() async throws {
        for index in activity.indices { activity[index].readAt = .now }
    }

    func loadAutomationPaused() async throws -> Bool? { nil }

    func setAutomationPaused(_ paused: Bool) async throws { self.paused = paused }

    func loadInstagramStatus() async throws -> InstagramStatus? { nil }

    func instagramAuthorizeURL() async throws -> URL {
        throw APIError.server(status: 409, message: "Connect your backend first (Settings → AI & backend).")
    }

    func syncInstagram() async throws -> Int { 0 }

    func disconnectInstagram(deleteMessages: Bool) async throws {}

    // MARK: Helpers

    private func append(_ message: InstagramMessage, to friendID: UUID) {
        guard let index = conversations.firstIndex(where: { $0.friend.id == friendID }) else { return }
        conversations[index].messages.append(message)
    }

    private func mutate(_ jobID: UUID, _ change: (inout ScheduledJob) -> Void) throws -> ScheduledJob {
        guard let index = jobs.firstIndex(where: { $0.id == jobID }) else {
            throw APIError.server(status: 404, message: "Job not found.")
        }
        change(&jobs[index])
        return jobs[index]
    }
}
