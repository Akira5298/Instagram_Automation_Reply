import Foundation
import Observation

/// Observable state for Messages and Scheduler: conversations, friends,
/// scheduled jobs and the activity feed. Delegates I/O to a `MessagingBackend`
/// (on-device sample data in mock mode, your server otherwise).
@MainActor
@Observable
final class MessagingStore {
    private(set) var conversations: [ConversationSummary] = []
    private(set) var jobs: [ScheduledJob] = []
    private(set) var activity: [ActivityItem] = []
    private(set) var isLoading = false
    var errorMessage: String?
    private(set) var instagramStatus: InstagramStatus?

    private let backend: MessagingBackend

    init(backend: MessagingBackend) {
        self.backend = backend
        if let mock = backend as? MockMessagingBackend {
            conversations = mock.conversations
            jobs = mock.jobs
        }
    }

    static func mock(now: Date = .now) -> MessagingStore {
        MessagingStore(backend: MockMessagingBackend(now: now))
    }

    var isRemote: Bool { backend.isRemote }
    var isMock: Bool { !backend.isRemote }
    var friends: [Friend] { conversations.map(\.friend) }
    var unreadActivityCount: Int { activity.filter { $0.readAt == nil }.count }
    /// Testing tools (simulate a friend's message, sample friends) only work
    /// with the mock Instagram provider.
    var usesMockInstagram: Bool { !isRemote || (instagramStatus?.usesMockInstagram ?? true) }

    // MARK: Loading

    func refresh() async {
        isLoading = true
        defer { isLoading = false }
        await perform {
            conversations = try await backend.loadConversations()
            jobs = try await backend.loadJobs()
            activity = try await backend.loadActivity()
        }
        instagramStatus = try? await backend.loadInstagramStatus()
    }

    // MARK: Instagram connection

    func refreshInstagramStatus() async {
        await perform { instagramStatus = try await backend.loadInstagramStatus() }
    }

    func instagramAuthorizeURL() async -> URL? {
        do {
            return try await backend.instagramAuthorizeURL()
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    func syncInstagram() async -> Int? {
        var count: Int?
        await perform {
            count = try await backend.syncInstagram()
            conversations = try await backend.loadConversations()
        }
        return count
    }

    func disconnectInstagram(deleteMessages: Bool) async {
        await perform { try await backend.disconnectInstagram(deleteMessages: deleteMessages) }
        await refresh()
    }

    func refreshJobs() async {
        await perform {
            jobs = try await backend.loadJobs()
            activity = try await backend.loadActivity()
        }
    }

    // MARK: Conversations

    func conversation(id: UUID) -> ConversationSummary? {
        conversations.first { $0.id == id }
    }

    /// Applies the change immediately, then saves it; rolls back on failure.
    func updateFriend(in conversationID: UUID, _ change: (inout Friend) -> Void) {
        guard let index = conversations.firstIndex(where: { $0.id == conversationID }) else { return }
        let previous = conversations[index].friend
        var updated = previous
        change(&updated)
        guard updated != previous else { return }
        conversations[index].friend = updated
        Task {
            do {
                let saved = try await backend.updateFriend(updated)
                if let i = conversations.firstIndex(where: { $0.id == conversationID }) {
                    conversations[i].friend = saved
                }
            } catch {
                if let i = conversations.firstIndex(where: { $0.id == conversationID }) {
                    conversations[i].friend = previous
                }
                errorMessage = error.localizedDescription
            }
        }
    }

    func markRead(_ conversationID: UUID) {
        guard let index = conversations.firstIndex(where: { $0.id == conversationID }) else { return }
        conversations[index].unreadCount = 0
    }

    @discardableResult
    func sendManualReply(_ text: String, in conversationID: UUID) async -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let conversation = conversation(id: conversationID) else { return false }
        return await perform {
            let message = try await backend.sendManualReply(trimmed, to: conversation.friend)
            if let index = conversations.firstIndex(where: { $0.id == conversationID }),
               !conversations[index].messages.contains(where: { $0.id == message.id }) {
                conversations[index].messages.append(message)
            }
        }
    }

    func simulateInbound(_ text: String, in conversationID: UUID) async {
        guard let conversation = conversation(id: conversationID) else { return }
        await perform {
            try await backend.simulateInbound(text, from: conversation.friend)
            conversations = try await backend.loadConversations()
        }
    }

    func seedSampleData() async {
        await perform { try await backend.seedSampleData() }
        await refresh()
    }

    // MARK: Jobs

    @discardableResult
    func createJob(_ draft: NewJobDraft) async -> Bool {
        await perform {
            let job = try await backend.createJob(draft)
            jobs.removeAll { $0.id == job.id }
            jobs.append(job)
        }
    }

    func togglePause(_ job: ScheduledJob) async {
        await perform {
            let updated = job.status == .paused ? try await backend.resume(job) : try await backend.pause(job)
            replace(updated)
        }
    }

    func cancel(_ job: ScheduledJob) async {
        await perform {
            try await backend.cancel(job)
            if let index = jobs.firstIndex(where: { $0.id == job.id }) { jobs[index].status = .cancelled }
        }
    }

    /// Removes the job from the list (cancelling it first if needed).
    func delete(_ job: ScheduledJob) async {
        if job.status != .cancelled { await cancel(job) }
        jobs.removeAll { $0.id == job.id }
    }

    func runNow(_ job: ScheduledJob) async {
        await perform { try await backend.runNow(job) }
        // Remote: the worker picks it up within a few seconds.
        if isRemote { try? await Task.sleep(for: .seconds(3)) }
        await refreshJobs()
    }

    func runs(for job: ScheduledJob) async -> [JobRun] {
        do {
            return try await backend.runs(for: job)
        } catch {
            errorMessage = error.localizedDescription
            return []
        }
    }

    func resolve(_ run: JobRun, action: JobRun.Action) async {
        await perform { try await backend.resolve(run, action: action) }
        await refreshJobs()
    }

    // MARK: Activity & automation

    func markActivityRead() async {
        await perform {
            try await backend.markActivityRead()
            activity = try await backend.loadActivity()
        }
    }

    func loadAutomationPaused() async -> Bool? {
        try? await backend.loadAutomationPaused()
    }

    @discardableResult
    func setAutomationPaused(_ paused: Bool) async -> Bool {
        await perform { try await backend.setAutomationPaused(paused) }
    }

    // MARK: Helpers

    private func replace(_ job: ScheduledJob) {
        if let index = jobs.firstIndex(where: { $0.id == job.id }) {
            jobs[index] = job
        } else {
            jobs.append(job)
        }
    }

    @discardableResult
    private func perform(_ operation: () async throws -> Void) async -> Bool {
        do {
            try await operation()
            return true
        } catch is CancellationError {
            return false
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }
}
