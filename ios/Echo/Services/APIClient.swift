import Foundation

enum APIError: LocalizedError, Equatable {
    case notConfigured
    case unauthorized
    case server(status: Int, message: String)
    case transport(String)
    case decoding(String)

    var errorDescription: String? {
        switch self {
        case .notConfigured:
            return "Backend isn't set up yet. Add the URL and token in Settings."
        case .unauthorized:
            return "The backend rejected the API token. Check it in Settings."
        case .server(_, let message):
            return message
        case .transport(let detail):
            return "Can't reach your backend (\(detail))."
        case .decoding(let detail):
            return "Unexpected response from the backend (\(detail))."
        }
    }

    /// Worth retrying later (network blips, rate limits, server hiccups).
    var isTransient: Bool {
        switch self {
        case .transport: return true
        case .server(let status, _): return status == 429 || status >= 500
        default: return false
        }
    }
}

/// Minimal async JSON client for the ECHO backend. snake_case on the wire,
/// camelCase in Swift. The bearer token comes from the Keychain.
struct APIClient: Sendable {
    let baseURL: URL
    let token: String
    let session: URLSession

    init(baseURL: URL, token: String, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.token = token
        self.session = session
    }

    func get<Response: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> Response {
        try decode(try await send("GET", path, query: query))
    }

    func post<Body: Encodable, Response: Decodable>(_ path: String, body: Body) async throws -> Response {
        try decode(try await send("POST", path, body: try Self.encoder.encode(body)))
    }

    func patch<Body: Encodable, Response: Decodable>(_ path: String, body: Body) async throws -> Response {
        try decode(try await send("PATCH", path, body: try Self.encoder.encode(body)))
    }

    func delete(_ path: String) async throws {
        _ = try await send("DELETE", path)
    }

    /// Returns the raw body; throws `APIError` for non-2xx responses.
    func send(_ method: String, _ path: String, query: [URLQueryItem] = [], body: Data? = nil) async throws -> Data {
        guard var components = URLComponents(url: baseURL.appending(path: path), resolvingAgainstBaseURL: false) else {
            throw APIError.notConfigured
        }
        if !query.isEmpty { components.queryItems = query }
        guard let url = components.url else { throw APIError.notConfigured }

        var request = URLRequest(url: url, timeoutInterval: 90)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        let result: (Data, URLResponse)
        do {
            result = try await session.data(for: request)
        } catch let error as URLError where error.code == .cancelled {
            throw CancellationError()
        } catch {
            throw APIError.transport(error.localizedDescription)
        }

        let (data, response) = result
        guard let http = response as? HTTPURLResponse else { throw APIError.transport("no HTTP response") }
        switch http.statusCode {
        case 200..<300:
            return data
        case 401:
            throw APIError.unauthorized
        default:
            throw APIError.server(status: http.statusCode, message: Self.detail(from: data) ?? "Server error \(http.statusCode).")
        }
    }

    private func decode<Response: Decodable>(_ data: Data) throws -> Response {
        do {
            return try Self.decoder.decode(Response.self, from: data)
        } catch {
            throw APIError.decoding(String(describing: error).prefix(160).description)
        }
    }

    /// FastAPI errors look like {"detail": "..."} (or a list for validation errors).
    private static func detail(from data: Data) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        if let text = object["detail"] as? String { return text }
        if let list = object["detail"] as? [[String: Any]], let first = list.first?["msg"] as? String { return first }
        return nil
    }

    static var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }

    static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let string = try container.decode(String.self)
            guard let date = ServerDate.parse(string) else {
                throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unrecognised date \(string)")
            }
            return date
        }
        return decoder
    }
}

/// Parses the backend's ISO-8601 timestamps, e.g.
/// "2026-11-11T11:05:57.313244+08:00" (microseconds) or "2026-11-11T03:05:57Z".
enum ServerDate {
    static func parse(_ string: String) -> Date? {
        // Trim fractional seconds to milliseconds; Foundation's parser is
        // only reliable with three digits.
        let normalised = string.replacingOccurrences(
            of: #"(\.\d{3})\d+"#,
            with: "$1",
            options: .regularExpression
        )
        let withFraction = ISO8601DateFormatter()
        withFraction.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = withFraction.date(from: normalised) { return date }
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        return plain.date(from: normalised)
    }

    static func string(from date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }
}
