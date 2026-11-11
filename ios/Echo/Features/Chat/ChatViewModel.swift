import Foundation
import Observation

@MainActor
@Observable
final class ChatViewModel {
    private(set) var messages: [ChatMessage]
    private(set) var isAwaitingReply = false
    private(set) var isLoadingHistory = false
    /// Non-blocking banner, e.g. "Can't reach your backend". Cached messages stay visible.
    private(set) var connectionProblem: String?
    var draft = ""
    var errorMessage: String?
    var toast: String?
    /// Called when a reply carries actions (e.g. ECHO scheduled a message),
    /// so other screens can refresh.
    var onActions: (([ChatAction]) -> Void)?

    private let service: ChatService
    private let profileStore: ProfileStore
    private let store: LocalJSONStore<[ChatMessage]>

    init(
        service: ChatService,
        profileStore: ProfileStore,
        store: LocalJSONStore<[ChatMessage]> = LocalJSONStore(filename: "chat_history.json")
    ) {
        self.service = service
        self.profileStore = profileStore
        self.store = store
        // A message still "sending" when the app was killed never got a reply.
        let saved = (store.load() ?? []).map { message -> ChatMessage in
            var message = message
            if message.deliveryState == .sending { message.deliveryState = .failed }
            return message
        }
        self.messages = saved.isEmpty ? [Self.greeting] : saved
    }

    var isRemote: Bool { service.isRemote }

    var canSend: Bool {
        !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isAwaitingReply
    }

    /// Learned-memory count shown in the header indicator.
    var memoryCount: Int { profileStore.traitCount }
    var lastLearnedAt: Date? { profileStore.lastLearnedAt }

    var recentLearnings: [MemoryUpdate] {
        Array(messages.reversed().flatMap(\.memoryUpdates).prefix(12))
    }

    /// Loads server history (backend mode). Mock mode keeps the local cache.
    func load() async {
        isLoadingHistory = true
        defer { isLoadingHistory = false }
        do {
            guard let remote = try await service.history() else { return }
            // Keep unsent local messages so you can retry them.
            let remoteIDs = Set(remote.map(\.id))
            let unsent = messages.filter { $0.deliveryState == .failed && !remoteIDs.contains($0.id) }
            messages = [Self.greeting] + remote + unsent
            connectionProblem = nil
            persist()
        } catch is CancellationError {
        } catch {
            connectionProblem = error.localizedDescription
        }
    }

    func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isAwaitingReply else { return }
        draft = ""
        let message = ChatMessage(role: .user, text: text, deliveryState: .sending)
        messages.append(message)
        await deliver(message.id)
    }

    func retry(_ message: ChatMessage) async {
        guard message.deliveryState == .failed, !isAwaitingReply else { return }
        await deliver(message.id)
    }

    private func deliver(_ id: UUID) async {
        guard let index = messages.firstIndex(where: { $0.id == id }) else { return }
        messages[index].deliveryState = .sending
        let outgoing = messages[index]
        isAwaitingReply = true
        persist()

        do {
            let reply = try await service.reply(to: outgoing, history: messages)
            setDeliveryState(nil, for: id)
            if !messages.contains(where: { $0.id == reply.id }) {
                messages.append(reply)
            }
            connectionProblem = nil
            isAwaitingReply = false
            persist()
            if !reply.actions.isEmpty { onActions?(reply.actions) }
            await profileStore.didLearn(reply.memoryUpdates)
        } catch {
            setDeliveryState(.failed, for: id)
            isAwaitingReply = false
            persist()
            if !(error is CancellationError) {
                errorMessage = error.localizedDescription
            }
        }
    }

    func clearConversation() async {
        do {
            try await service.clearHistory()
            messages = [Self.greeting]
            store.delete()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Long-press → "Use as writing example".
    func saveAsExample(_ message: ChatMessage) async {
        guard message.role == .user else { return }
        if await profileStore.addExample(text: message.text, language: message.language) {
            toast = "Saved as a writing example"
        } else {
            errorMessage = profileStore.errorMessage
        }
    }

    /// Wipes only the on-device copy (used by "Delete all data on this device").
    func resetLocalCache() {
        messages = [Self.greeting]
        store.delete()
    }

    private func setDeliveryState(_ state: ChatMessage.DeliveryState?, for id: UUID) {
        if let index = messages.firstIndex(where: { $0.id == id }) {
            messages[index].deliveryState = state
        }
    }

    private func persist() {
        try? store.save(messages)
    }

    static let greeting = ChatMessage(
        id: UUID(uuidString: "00000000-0000-0000-0000-00000000EC40") ?? UUID(),
        role: .assistant,
        text: "Hey, I'm ECHO 👋 Talk to me about anything — your day, plans, what you're into. "
            + "I'll quietly learn how you text, and you can see or change everything I learn in Personality. "
            + "English, 日本語, Italiano or 中文 all work.",
        createdAt: Date(timeIntervalSince1970: 0)
    )
}
