import Foundation

/// The chat screen talks to this protocol only.
///  * `MockChatService` — offline, on-device heuristics (no server needed).
///  * `BackendChatService` — your FastAPI server, which calls Claude.
/// The Claude API key never ships inside the app.
@MainActor
protocol ChatService {
    var isRemote: Bool { get }

    /// Server-side history, oldest first. `nil` means "no server history;
    /// use the on-device cache" (mock mode).
    func history() async throws -> [ChatMessage]?

    /// Sends your message (its `id` is the idempotency key) and returns
    /// ECHO's reply, including what was learned.
    func reply(to message: ChatMessage, history: [ChatMessage]) async throws -> ChatMessage

    func clearHistory() async throws
}

enum ChatServiceError: LocalizedError {
    case emptyMessage

    var errorDescription: String? {
        switch self {
        case .emptyMessage: return "Message is empty."
        }
    }
}
