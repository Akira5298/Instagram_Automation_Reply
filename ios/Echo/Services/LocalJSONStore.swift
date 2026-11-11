import Foundation

/// Small Codable file store in Application Support, written atomically with
/// iOS Data Protection (encrypted while the device is locked).
/// Phase 2 moves the source of truth to the backend; this stays as a cache.
struct LocalJSONStore<Value: Codable> {
    let fileURL: URL

    init(filename: String, directory: URL = .applicationSupportDirectory) {
        self.fileURL = directory.appending(path: filename)
    }

    func load() -> Value? {
        guard let data = try? Data(contentsOf: fileURL) else { return nil }
        return try? Self.decoder.decode(Value.self, from: data)
    }

    func save(_ value: Value) throws {
        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let data = try Self.encoder.encode(value)
        try data.write(to: fileURL, options: [.atomic, .completeFileProtection])
    }

    func delete() {
        try? FileManager.default.removeItem(at: fileURL)
    }

    private static var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return encoder
    }

    private static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}
