import Foundation
import Observation

@MainActor
@Observable
final class MessagesViewModel {
    var searchText = ""
    let store: MessagingStore

    init(store: MessagingStore) {
        self.store = store
    }

    var isMock: Bool { store.isMock }

    var conversations: [ConversationSummary] {
        let sorted = store.conversations.sorted {
            ($0.lastMessage?.sentAt ?? .distantPast) > ($1.lastMessage?.sentAt ?? .distantPast)
        }
        let query = searchText.trimmingCharacters(in: .whitespaces)
        guard !query.isEmpty else { return sorted }
        return sorted.filter {
            $0.friend.displayName.localizedCaseInsensitiveContains(query)
                || $0.friend.username.localizedCaseInsensitiveContains(query)
        }
    }

    var automatedCount: Int { store.friends.filter(\.aiReplyEnabled).count }

    static func windowText(for friend: Friend, now: Date = .now) -> String {
        guard let remaining = friend.standardWindowRemaining(now: now) else {
            return friend.lastInboundAt == nil ? "No messages from them yet" : "Reply window closed"
        }
        let hours = Int(remaining / 3600)
        return hours >= 1 ? "Window: \(hours)h left" : "Window: <1h left"
    }
}
