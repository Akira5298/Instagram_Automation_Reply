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
    /// Drafts waiting for your OK (Phase 5 review queue).
    private(set) var reviews: [ReviewItem] = []

    private let backend: MessagingBackend

    // Bookkeeping, not UI state: kept out of observation so touching it never re-renders views.
    /// The chat that's open on screen; live reloads keep it marked as read.
    @ObservationIgnored private var openConversationID: UUID?
    /// The refresh in flight, so overlapping callers share it instead of racing.
    @ObservationIgnored private var refreshInFlight: Task<Void, Never>?
    /// Only the newest live-update loop keeps running (see runLiveUpdates).
    @ObservationIgnored private var liveGeneration = 0

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
    func pendingReviews(for friendID: UUID) -> Int { reviews.filter { $0.friendId == friendID }.count }
    /// Testing tools (simulate a friend's message, sample friends) only work
    /// with the mock Instagram provider.
    var usesMockInstagram: Bool { !isRemote || (instagramStatus?.usesMockInstagram ?? true) }

    // MARK: Loading

    /// Full reload. Concurrent callers (launch, pull-to-refresh, returning from
    /// the background, a tapped notification) share one request instead of
    /// racing each other, and the results land in a single update.
    func refresh() async {
        if let running = refreshInFlight {
            await running.value
            return
        }
        let task = Task { await self.performRefresh() }
        refreshInFlight = task
        await task.value
        refreshInFlight = nil
    }

    private func performRefresh() async {
        isLoading = true
        defer { isLoading = false }
        // Fetch everything first, then apply it together, so views get one
        // consistent update instead of one per request.
        let core: (conversations: [ConversationSummary], jobs: [ScheduledJob], activity: [ActivityItem])?
        do {
            let conversations = try await backend.loadConversations()
            let jobs = try await backend.loadJobs()
            let activity = try await backend.loadActivity()
            core = (conversations, jobs, activity)
        } catch is CancellationError {
            return
        } catch {
            core = nil
            errorMessage = error.localizedDescription
        }
        let loadedReviews = try? await backend.loadReviews()
        let loadedStatus = try? await backend.loadInstagramStatus()

        if let core {
            apply(conversations: core.conversations)
            set(\.jobs, core.jobs)
            set(\.activity, core.activity)
        }
        if let loadedReviews { set(\.reviews, loadedReviews) }
        if let loadedStatus { set(\.instagramStatus, loadedStatus) }
    }

    /// Assigning an @Observable property always notifies its observers, even
    /// with an identical value. Live updates and refreshes mostly bring back
    /// the same data, so only assign real changes.
    private func set<Value: Equatable>(_ keyPath: ReferenceWritableKeyPath<MessagingStore, Value>, _ value: Value) {
        if self[keyPath: keyPath] != value { self[keyPath: keyPath] = value }
    }

    private func apply(conversations loaded: [ConversationSummary]) {
        var loaded = loaded
        if let open = openConversationID, let index = loaded.firstIndex(where: { $0.id == open }) {
            loaded[index].unreadCount = 0     // you're looking at it
        }
        set(\.conversations, loaded)
    }

    // MARK: Instagram connection

    func refreshInstagramStatus() async {
        await perform {
            let status = try await backend.loadInstagramStatus()
            set(\.instagramStatus, status)
        }
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
            let loaded = try await backend.loadConversations()
            apply(conversations: loaded)
        }
        return count
    }

    func disconnectInstagram(deleteMessages: Bool) async {
        await perform { try await backend.disconnectInstagram(deleteMessages: deleteMessages) }
        await refresh()
    }

    func refreshJobs() async {
        await perform {
            let loadedJobs = try await backend.loadJobs()
            let loadedActivity = try await backend.loadActivity()
            set(\.jobs, loadedJobs)
            set(\.activity, loadedActivity)
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
        guard let index = conversations.firstIndex(where: { $0.id == conversationID }),
              conversations[index].unreadCount != 0 else { return }   // no-op writes still re-render
        conversations[index].unreadCount = 0
    }

    /// The conversation screen calls these so live reloads don't flip its
    /// unread badge back on while you're reading it.
    func conversationOpened(_ conversationID: UUID) {
        openConversationID = conversationID
        markRead(conversationID)
    }

    func conversationClosed(_ conversationID: UUID) {
        if openConversationID == conversationID { openConversationID = nil }
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
            let loadedConversations = try await backend.loadConversations()
            let loadedReviews = try await backend.loadReviews()
            let loadedActivity = try await backend.loadActivity()
            apply(conversations: loadedConversations)
            set(\.reviews, loadedReviews)
            set(\.activity, loadedActivity)
        }
    }

    // MARK: Push, notification preferences, memories (Phase 6)

    private var registeredToken: String?

    /// Sends the APNs token to your server (once per token per launch).
    func registerDevice(token: String) async {
        guard isRemote, token != registeredToken else { return }
        do {
            try await backend.registerDevice(token: token, environment: PushManager.environment)
            registeredToken = token
        } catch {
            // Quietly retried next launch; the server may simply be offline.
        }
    }

    func sendTestPush() async -> String {
        do {
            return try await backend.sendTestPush()
        } catch {
            return error.localizedDescription
        }
    }

    func loadNotificationPreferences() async -> NotificationPreferences? {
        try? await backend.loadNotificationPreferences()
    }

    func saveNotificationPreferences(_ preferences: NotificationPreferences) async {
        await perform { try await backend.saveNotificationPreferences(preferences) }
    }

    func memories(for friend: Friend) async -> [FriendMemory] {
        do {
            return try await backend.loadMemories(for: friend)
        } catch {
            errorMessage = error.localizedDescription
            return []
        }
    }

    func addMemory(_ content: String, sensitive: Bool, for friend: Friend) async -> FriendMemory? {
        var created: FriendMemory?
        await perform { created = try await backend.addMemory(content, sensitive: sensitive, for: friend) }
        return created
    }

    func updateMemory(_ memory: FriendMemory) async -> FriendMemory? {
        var updated: FriendMemory?
        await perform { updated = try await backend.updateMemory(memory) }
        return updated
    }

    @discardableResult
    func deleteMemory(_ memory: FriendMemory) async -> Bool {
        await perform { try await backend.deleteMemory(memory) }
    }

    // MARK: Live updates

    /// Keeps one long-poll request open while the app is in the foreground and
    /// reloads messages, drafts and activity the moment the server has news
    /// (a new DM, an automatic reply, a held draft…). Returns when cancelled.
    func runLiveUpdates() async {
        guard isRemote else { return }
        // Exactly one loop: a newer call (the view's task restarting) retires
        // any older loop that hasn't noticed its cancellation yet.
        liveGeneration &+= 1
        let generation = liveGeneration
        var cursor: String?
        var failures = 0
        while !Task.isCancelled, generation == liveGeneration {
            do {
                let update = try await backend.waitForUpdates(since: cursor)
                guard !Task.isCancelled, generation == liveGeneration else { return }
                failures = 0
                if update.changed && cursor != nil { await reloadQuietly() }
                cursor = update.cursor
            } catch {
                if Task.isCancelled { return }
                failures += 1
                try? await Task.sleep(for: .seconds(min(30, 2 * failures)))   // offline / Mac asleep
            }
        }
    }

    /// Like refresh(), but never shows an error banner (it runs in the background).
    /// Fetches first, then applies only what changed, in one go.
    private func reloadQuietly() async {
        let loadedConversations = try? await backend.loadConversations()
        let loadedReviews = try? await backend.loadReviews()
        let loadedActivity = try? await backend.loadActivity()
        if let loadedConversations { apply(conversations: loadedConversations) }
        if let loadedReviews { set(\.reviews, loadedReviews) }
        if let loadedActivity { set(\.activity, loadedActivity) }
    }

    // MARK: Automatic replies

    func refreshReviews() async {
        await perform {
            let loaded = try await backend.loadReviews()
            set(\.reviews, loaded)
        }
    }

    /// Send (optionally edited), dismiss, or mark as answered in Instagram.
    @discardableResult
    func resolveReview(_ item: ReviewItem, action: ReviewItem.Action, text: String? = nil,
                       kind: ReviewItem.Suggestion.Kind = .text) async -> Bool {
        let ok = await perform {
            try await backend.resolveReview(item, action: action, text: text, kind: kind)
            reviews.removeAll { $0.id == item.id }
        }
        if ok, action == .send {
            await perform {
                let loaded = try await backend.loadConversations()
                apply(conversations: loaded)
            }
            if isRemote { await refreshJobs() }
        }
        return ok
    }

    /// "More options" on a held reply: ECHO writes a few more in your voice.
    func moreSuggestions(for item: ReviewItem) async {
        await perform {
            let fresh = try await backend.moreSuggestions(for: item)
            if let index = reviews.firstIndex(where: { $0.id == item.id }) { reviews[index].suggestions = fresh }
        }
    }

    func setTakenOver(_ takenOver: Bool, in conversationID: UUID) async {
        guard let friend = conversation(id: conversationID)?.friend else { return }
        await perform {
            let saved = takenOver ? try await backend.takeOver(friend) : try await backend.handBack(friend)
            replaceFriend(saved)
        }
    }

    @discardableResult
    func requestConsent(in conversationID: UUID) async -> Bool {
        guard let friend = conversation(id: conversationID)?.friend else { return false }
        return await perform {
            replaceFriend(try await backend.requestConsent(friend))
            let loaded = try await backend.loadConversations()
            apply(conversations: loaded)
        }
    }

    func style(for friend: Friend) async -> FriendStyle? {
        do {
            return try await backend.loadStyle(for: friend)
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    @discardableResult
    func saveStyle(_ style: FriendStyle, for friend: Friend) async -> Bool {
        await perform { _ = try await backend.saveStyle(style, for: friend) }
    }

    func learnStyleNow(for friend: Friend) async -> FriendStyle? {
        var learned: FriendStyle?
        await perform { learned = try await backend.learnStyleNow(for: friend) }
        return learned
    }

    @discardableResult
    func forgetLearnedStyle(for friend: Friend) async -> Bool {
        await perform { try await backend.forgetLearnedStyle(for: friend) }
    }

    /// Ideas for a scheduled message, in your voice for this relationship.
    func messageIdeas(for friend: Friend, occasion: String?) async -> [MessageIdea] {
        var ideas: [MessageIdea] = []
        await perform { ideas = try await backend.messageIdeas(for: friend, occasion: occasion, count: 4) }
        return ideas
    }

    func uploadContext(_ context: UserContextUpload) async {
        try? await backend.uploadContext(context)    // background: never shows an error banner
    }

    func clearContext() async {
        try? await backend.clearContext()
    }

    func loadAutomationSettings() async -> AutomationSettings? {
        try? await backend.loadAutomationSettings()
    }

    @discardableResult
    func saveAutomationSettings(_ settings: AutomationSettings) async -> AutomationSettings? {
        var saved: AutomationSettings?
        await perform { saved = try await backend.saveAutomationSettings(settings) }
        return saved
    }

    private func replaceFriend(_ friend: Friend) {
        if let index = conversations.firstIndex(where: { $0.friend.id == friend.id }) {
            conversations[index].friend = friend
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
            let loaded = try await backend.loadActivity()
            set(\.activity, loaded)
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
