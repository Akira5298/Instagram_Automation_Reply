import Foundation

/// Where the communication profile lives.
///  * `LocalProfileRepository` — on-device (mock mode). Applies the memory
///    rules itself.
///  * `RemoteProfileRepository` — your backend, which applies the same rules
///    server-side; the app just reloads.
/// Every mutation returns the full, updated profile.
@MainActor
protocol ProfileRepository {
    var isRemote: Bool { get }
    func load() async throws -> CommunicationProfile
    func didLearn(_ updates: [MemoryUpdate]) async throws -> CommunicationProfile
    func save(_ trait: ProfileTrait) async throws -> CommunicationProfile
    func addTrait(category: ProfileCategory, value: String) async throws -> CommunicationProfile
    func delete(_ trait: ProfileTrait) async throws -> CommunicationProfile
    func deleteAll() async throws -> CommunicationProfile
    func addExample(text: String, language: String?) async throws -> CommunicationProfile
    func deleteExample(_ example: WritingExample) async throws -> CommunicationProfile
    func exportJSON() async throws -> String
}

// MARK: - Local (mock mode)

/// Rules (mirrors backend/app/domain/memory.py):
///  * Stated facts are stored as facts; observations start ≤ 0.6 and each
///    repeat closes 25% of the gap to certainty, capped at 0.95.
///  * Your edits always win — inference never overwrites an edited trait.
///  * A trait you delete is remembered as rejected and is not re-learned.
@MainActor
final class LocalProfileRepository: ProfileRepository {
    private(set) var profile: CommunicationProfile
    private let store: LocalJSONStore<CommunicationProfile>

    init(store: LocalJSONStore<CommunicationProfile> = LocalJSONStore(filename: "profile.json")) {
        self.store = store
        self.profile = store.load() ?? CommunicationProfile()
    }

    var isRemote: Bool { false }

    func load() async throws -> CommunicationProfile { profile }

    func didLearn(_ updates: [MemoryUpdate]) async throws -> CommunicationProfile {
        for update in updates where !profile.rejectedKeys.contains(update.key) {
            let existingIndex = profile.traits.firstIndex { $0.key == update.key }

            if update.isRemoval {
                if let existingIndex, profile.traits[existingIndex].source != .userEdited {
                    profile.traits.remove(at: existingIndex)
                }
                continue
            }

            guard let index = existingIndex else {
                let stated = update.kind == .statedFact
                profile.traits.append(ProfileTrait(
                    category: update.category,
                    key: update.key,
                    value: update.summary,
                    source: stated ? .userStated : .inferred,
                    confidence: stated ? 1 : min(update.confidence, 0.6)
                ))
                continue
            }

            var trait = profile.traits[index]
            guard trait.source != .userEdited else { continue }
            trait.evidenceCount += 1
            if update.kind == .statedFact {
                trait.source = .userStated
                trait.confidence = 1
                trait.value = update.summary
            } else if trait.source == .inferred {
                trait.confidence = min(0.95, trait.confidence + (1 - trait.confidence) * 0.25)
                trait.value = update.summary
            }
            trait.updatedAt = .now
            profile.traits[index] = trait
        }
        return try persist()
    }

    func save(_ trait: ProfileTrait) async throws -> CommunicationProfile {
        var edited = trait
        edited.source = .userEdited
        edited.confidence = 1
        edited.updatedAt = .now
        if let index = profile.traits.firstIndex(where: { $0.id == trait.id }) {
            profile.traits[index] = edited
        } else {
            profile.traits.append(edited)
        }
        return try persist()
    }

    func addTrait(category: ProfileCategory, value: String) async throws -> CommunicationProfile {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return profile }
        return try await save(ProfileTrait(
            category: category,
            key: "\(category.rawValue).manual_\(trimmed.lowercased())",
            value: trimmed,
            source: .userEdited,
            confidence: 1
        ))
    }

    func delete(_ trait: ProfileTrait) async throws -> CommunicationProfile {
        profile.traits.removeAll { $0.id == trait.id }
        profile.rejectedKeys.insert(trait.key)
        return try persist()
    }

    func deleteAll() async throws -> CommunicationProfile {
        profile = CommunicationProfile()
        store.delete()
        return profile
    }

    func addExample(text: String, language: String?) async throws -> CommunicationProfile {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !profile.examples.contains(where: { $0.text == trimmed }) else { return profile }
        profile.examples.insert(WritingExample(text: trimmed, language: language), at: 0)
        return try persist()
    }

    func deleteExample(_ example: WritingExample) async throws -> CommunicationProfile {
        profile.examples.removeAll { $0.id == example.id }
        return try persist()
    }

    func exportJSON() async throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .iso8601
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return String(decoding: try encoder.encode(profile), as: UTF8.self)
    }

    private func persist() throws -> CommunicationProfile {
        try store.save(profile)
        return profile
    }
}

// MARK: - Remote (backend mode)

@MainActor
final class RemoteProfileRepository: ProfileRepository {
    private let api: APIClient
    /// Last good copy, shown instantly at launch and when offline.
    private let cache: LocalJSONStore<CommunicationProfile>

    init(api: APIClient, cache: LocalJSONStore<CommunicationProfile> = LocalJSONStore(filename: "profile-remote-cache.json")) {
        self.api = api
        self.cache = cache
    }

    var isRemote: Bool { true }
    var cachedProfile: CommunicationProfile? { cache.load() }

    private struct TraitCreate: Encodable { let category: ProfileCategory; let value: String }
    private struct TraitPatch: Encodable { let category: ProfileCategory; let value: String }
    private struct ExampleCreate: Encodable { let text: String; let language: String?; let source: String }
    private struct Ignored: Decodable {}

    func load() async throws -> CommunicationProfile {
        let profile: CommunicationProfile = try await api.get("v1/profile")
        try? cache.save(profile)
        return profile
    }

    func didLearn(_ updates: [MemoryUpdate]) async throws -> CommunicationProfile {
        try await load() // the server already merged them
    }

    func save(_ trait: ProfileTrait) async throws -> CommunicationProfile {
        let _: Ignored = try await api.patch(
            "v1/profile/traits/\(trait.id.uuidString.lowercased())",
            body: TraitPatch(category: trait.category, value: trait.value)
        )
        return try await load()
    }

    func addTrait(category: ProfileCategory, value: String) async throws -> CommunicationProfile {
        let _: Ignored = try await api.post("v1/profile/traits", body: TraitCreate(category: category, value: value))
        return try await load()
    }

    func delete(_ trait: ProfileTrait) async throws -> CommunicationProfile {
        try await api.delete("v1/profile/traits/\(trait.id.uuidString.lowercased())")
        return try await load()
    }

    func deleteAll() async throws -> CommunicationProfile {
        try await api.delete("v1/profile")
        cache.delete()
        return try await load()
    }

    func addExample(text: String, language: String?) async throws -> CommunicationProfile {
        let _: Ignored = try await api.post(
            "v1/profile/examples",
            body: ExampleCreate(text: text, language: language, source: "chat")
        )
        return try await load()
    }

    func deleteExample(_ example: WritingExample) async throws -> CommunicationProfile {
        try await api.delete("v1/profile/examples/\(example.id.uuidString.lowercased())")
        return try await load()
    }

    func exportJSON() async throws -> String {
        let data = try await api.send("GET", "v1/profile/export")
        if let object = try? JSONSerialization.jsonObject(with: data),
           let pretty = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]) {
            return String(decoding: pretty, as: UTF8.self)
        }
        return String(decoding: data, as: UTF8.self)
    }
}
