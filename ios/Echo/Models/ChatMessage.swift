import Foundation

struct ChatMessage: Identifiable, Codable, Hashable, Sendable {
    enum Role: String, Codable, Sendable {
        case user
        case assistant
    }

    /// Local-only send state for your own messages (never sent by the server).
    enum DeliveryState: String, Codable, Sendable {
        case sending
        case failed
    }

    /// For your messages this id doubles as the idempotency key sent to the
    /// backend (`client_msg_id`), so retries never create duplicates.
    let id: UUID
    let role: Role
    var text: String
    let createdAt: Date
    /// Language detected for this message (BCP-47), when known.
    var language: String?
    /// What ECHO learned from the turn that produced this reply.
    var memoryUpdates: [MemoryUpdate]
    var deliveryState: DeliveryState?
    /// Things ECHO did during this turn, e.g. scheduling a message.
    var actions: [ChatAction] = []

    init(
        id: UUID = UUID(),
        role: Role,
        text: String,
        createdAt: Date = .now,
        language: String? = nil,
        memoryUpdates: [MemoryUpdate] = [],
        deliveryState: DeliveryState? = nil,
        actions: [ChatAction] = []
    ) {
        self.id = id
        self.role = role
        self.text = text
        self.createdAt = createdAt
        self.language = language
        self.memoryUpdates = memoryUpdates
        self.deliveryState = deliveryState
        self.actions = actions
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(UUID.self, forKey: .id)
        role = try c.decode(Role.self, forKey: .role)
        text = try c.decode(String.self, forKey: .text)
        createdAt = try c.decode(Date.self, forKey: .createdAt)
        language = try c.decodeIfPresent(String.self, forKey: .language)
        memoryUpdates = try c.decodeIfPresent([MemoryUpdate].self, forKey: .memoryUpdates) ?? []
        deliveryState = try c.decodeIfPresent(DeliveryState.self, forKey: .deliveryState)
        actions = (try? c.decodeIfPresent([ChatAction].self, forKey: .actions)) ?? []
    }
}

/// One thing ECHO noticed or was told during a conversation.
/// Raw values match the backend's JSON.
struct MemoryUpdate: Identifiable, Codable, Hashable, Sendable {
    enum Kind: String, Codable, Sendable {
        /// You said it explicitly ("I love bouldering").
        case statedFact = "stated_fact"
        /// A tentative inference ("seems to write short messages").
        /// Never presented or used as an established fact.
        case observation
    }

    enum Op: String, Codable, Sendable {
        case set
        /// "I'm not into anime anymore" — the trait was archived.
        case remove
    }

    let id: UUID
    var op: Op?
    let category: ProfileCategory
    /// Stable key used to merge repeated evidence, e.g. "slang.ngl".
    let key: String
    let summary: String
    let kind: Kind
    let confidence: Double

    init(
        id: UUID = UUID(),
        op: Op? = .set,
        category: ProfileCategory,
        key: String,
        summary: String,
        kind: Kind,
        confidence: Double
    ) {
        self.id = id
        self.op = op
        self.category = category
        self.key = key
        self.summary = summary
        self.kind = kind
        self.confidence = confidence
    }

    var isRemoval: Bool { op == .remove }
}

/// A card under ECHO's reply, e.g. "Scheduled: Kenji · Thu 8:00 PM".
/// Every field is optional so new action types never break decoding.
struct ChatAction: Codable, Hashable, Sendable {
    var type: String
    var jobId: String?
    var recipientName: String?
    var scheduledAt: Date?
    var timeZoneId: String?
    var recurrenceTitle: String?
    var automationEnabled: Bool?
    var instruction: String?
    var reason: String?

    var isScheduledJob: Bool { type == "scheduled_job" }

    var formattedWhen: String? {
        guard let scheduledAt else { return nil }
        var style = Date.FormatStyle.dateTime.weekday(.abbreviated).day().month(.abbreviated).hour().minute()
        style.timeZone = timeZoneId.flatMap(TimeZone.init(identifier:)) ?? .current
        return scheduledAt.formatted(style)
    }
}
