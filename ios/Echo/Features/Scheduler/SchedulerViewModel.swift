import Foundation
import Observation

@MainActor
@Observable
final class SchedulerViewModel {
    enum Filter: String, CaseIterable, Identifiable {
        case all, upcoming, sent, attention

        var id: String { rawValue }

        var title: String {
            switch self {
            case .all: return "All"
            case .upcoming: return "Upcoming"
            case .sent: return "Sent"
            case .attention: return "Issues"
            }
        }
    }

    var filter: Filter = .all
    /// nil = show every day.
    var selectedDay: Date?
    let store: MessagingStore
    let timeZone: TimeZone

    init(store: MessagingStore, timeZoneID: String = "Asia/Kuala_Lumpur") {
        self.store = store
        self.timeZone = TimeZone(identifier: timeZoneID) ?? .current
    }

    var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        return calendar
    }

    /// Formats in the scheduling timezone, not the phone's.
    func format(_ date: Date, _ style: Date.FormatStyle) -> String {
        var style = style
        style.timeZone = timeZone
        return date.formatted(style)
    }

    /// Today + next 6 days, in the scheduling timezone.
    func weekDays(from now: Date = .now) -> [Date] {
        let start = calendar.startOfDay(for: now)
        return (0..<7).compactMap { calendar.date(byAdding: .day, value: $0, to: start) }
    }

    func jobCount(on day: Date) -> Int {
        store.jobs.filter { calendar.isDate($0.scheduledAt, inSameDayAs: day) && $0.status != .cancelled }.count
    }

    var visibleJobs: [ScheduledJob] {
        store.jobs
            .filter { job in
                switch filter {
                case .all: return true
                case .upcoming: return job.status.isUpcoming
                case .sent: return job.status == .sent
                case .attention: return job.status.isIssue || job.lastResultNote?.isIssue == true
                }
            }
            .filter { job in
                guard let selectedDay else { return true }
                return calendar.isDate(job.scheduledAt, inSameDayAs: selectedDay)
            }
            .sorted { $0.scheduledAt < $1.scheduledAt }
    }

    var issueCount: Int {
        store.jobs.filter { $0.status.isIssue || ($0.status == .scheduled && $0.lastRunStatus?.isIssue == true) }.count
    }

    func select(day: Date) {
        if let selectedDay, calendar.isDate(selectedDay, inSameDayAs: day) {
            self.selectedDay = nil
        } else {
            selectedDay = day
        }
    }

    func togglePause(_ job: ScheduledJob) async { await store.togglePause(job) }
    func cancel(_ job: ScheduledJob) async { await store.cancel(job) }
    func delete(_ job: ScheduledJob) async { await store.delete(job) }
    func runNow(_ job: ScheduledJob) async { await store.runNow(job) }
    func refresh() async { await store.refresh() }

    func ideas(for friend: Friend, occasion: String) async -> [MessageIdea] {
        let trimmed = occasion.trimmingCharacters(in: .whitespacesAndNewlines)
        return await store.messageIdeas(for: friend, occasion: trimmed.isEmpty ? nil : trimmed)
    }

    var errorMessage: String? {
        get { store.errorMessage }
        set { store.errorMessage = newValue }
    }

    /// Creates a job. This only *schedules* it: whether Instagram allows the
    /// message is decided by the backend's checks at dispatch time.
    @discardableResult
    func createJob(friend: Friend, date: Date, recurrence: ScheduledJob.Recurrence, instruction: String,
                   automationEnabled: Bool, messageText: String? = nil) async -> Bool {
        let exact = messageText?.trimmingCharacters(in: .whitespacesAndNewlines)
        return await store.createJob(NewJobDraft(
            friend: friend,
            scheduledAt: date,
            timeZoneID: timeZone.identifier,
            recurrence: recurrence,
            instruction: instruction.trimmingCharacters(in: .whitespacesAndNewlines),
            automationEnabled: automationEnabled,
            messageText: (exact?.isEmpty ?? true) ? nil : exact
        ))
    }
}
