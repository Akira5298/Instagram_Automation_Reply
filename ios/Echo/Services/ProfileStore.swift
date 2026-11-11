import Foundation
import Observation

/// Observable owner of the global communication profile (memory level 1).
/// Delegates storage and the memory rules to a `ProfileRepository`
/// (on-device in mock mode, your backend otherwise).
@MainActor
@Observable
final class ProfileStore {
    private(set) var profile: CommunicationProfile
    private(set) var lastLearnedAt: Date?
    private(set) var isLoading = false
    var errorMessage: String?

    private let repository: ProfileRepository

    init(repository: ProfileRepository, initial: CommunicationProfile = CommunicationProfile()) {
        self.repository = repository
        self.profile = initial
    }

    var isRemote: Bool { repository.isRemote }
    var traitCount: Int { profile.traits.count }

    func refresh() async {
        isLoading = true
        defer { isLoading = false }
        await perform { try await self.repository.load() }
    }

    /// Called after each chat turn with what ECHO learned.
    func didLearn(_ updates: [MemoryUpdate]) async {
        guard !updates.isEmpty else { return }
        let before = Set(profile.traits.map(\.key))
        await perform { try await self.repository.didLearn(updates) }
        if !Set(profile.traits.map(\.key)).subtracting(before).isEmpty || updates.contains(where: { !$0.isRemoval }) {
            lastLearnedAt = .now
        }
    }

    @discardableResult
    func save(_ trait: ProfileTrait) async -> Bool {
        await perform { try await self.repository.save(trait) }
    }

    @discardableResult
    func addTrait(category: ProfileCategory, value: String) async -> Bool {
        await perform { try await self.repository.addTrait(category: category, value: value) }
    }

    @discardableResult
    func delete(_ trait: ProfileTrait) async -> Bool {
        // Optimistic: hide it immediately, roll back on failure.
        let previous = profile
        profile.traits.removeAll { $0.id == trait.id }
        let ok = await perform { try await self.repository.delete(trait) }
        if !ok { profile = previous }
        return ok
    }

    @discardableResult
    func deleteAll() async -> Bool {
        let ok = await perform { try await self.repository.deleteAll() }
        if ok { lastLearnedAt = nil }
        return ok
    }

    @discardableResult
    func addExample(text: String, language: String?) async -> Bool {
        await perform { try await self.repository.addExample(text: text, language: language) }
    }

    @discardableResult
    func deleteExample(_ example: WritingExample) async -> Bool {
        await perform { try await self.repository.deleteExample(example) }
    }

    func exportJSON() async -> String? {
        do {
            return try await repository.exportJSON()
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    @discardableResult
    private func perform(_ operation: () async throws -> CommunicationProfile) async -> Bool {
        do {
            profile = try await operation()
            errorMessage = nil
            return true
        } catch is CancellationError {
            return false
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }
}
