import Foundation

/// Talks to your ECHO backend (`/v1/chat/*`), which calls Claude.
@MainActor
final class BackendChatService: ChatService {
    private let api: APIClient

    init(api: APIClient) {
        self.api = api
    }

    var isRemote: Bool { true }

    private struct HistoryResponse: Decodable {
        let messages: [ChatMessage]
        let hasMore: Bool
    }

    private struct TurnRequest: Encodable {
        let clientMsgId: UUID
        let text: String
    }

    private struct TurnResponse: Decodable {
        let userMessage: ChatMessage
        let message: ChatMessage
        let replayed: Bool
    }

    func history() async throws -> [ChatMessage]? {
        let response: HistoryResponse = try await api.get(
            "v1/chat/messages",
            query: [URLQueryItem(name: "limit", value: "100")]
        )
        return response.messages
    }

    func reply(to message: ChatMessage, history: [ChatMessage]) async throws -> ChatMessage {
        let text = message.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { throw ChatServiceError.emptyMessage }
        // The server builds context from its own records, so `history` isn't sent.
        let response: TurnResponse = try await api.post(
            "v1/chat/messages",
            body: TurnRequest(clientMsgId: message.id, text: text)
        )
        return response.message
    }

    func clearHistory() async throws {
        try await api.delete("v1/chat/messages")
    }
}
