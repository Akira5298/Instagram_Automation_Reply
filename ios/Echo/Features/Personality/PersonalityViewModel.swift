import Foundation
import Observation

@MainActor
@Observable
final class PersonalityViewModel {
    var editingTrait: ProfileTrait?
    var isAddingTrait = false
    var confirmingDeleteAll = false
    var export: ExportedProfile?
    private(set) var isExporting = false

    let profileStore: ProfileStore

    init(profileStore: ProfileStore) {
        self.profileStore = profileStore
    }

    struct ExportedProfile: Identifiable {
        let id = UUID()
        let json: String
    }

    var profile: CommunicationProfile { profileStore.profile }
    var isRemote: Bool { profileStore.isRemote }
    var isLoading: Bool { profileStore.isLoading }

    var errorMessage: String? {
        get { profileStore.errorMessage }
        set { profileStore.errorMessage = newValue }
    }

    var populatedCategories: [ProfileCategory] {
        ProfileCategory.allCases.filter { !profile.traits(in: $0).isEmpty }
    }

    var languages: [ProfileTrait] { profile.traits(in: .languages) }
    var examples: [WritingExample] { profile.examples }

    var confirmedCount: Int { profile.traits.filter { !$0.isTentative }.count }
    var tentativeCount: Int { profile.traits.filter(\.isTentative).count }

    /// 0...1 — a rough "how well ECHO knows your style" gauge.
    var profileStrength: Double {
        let categoriesCovered = Double(populatedCategories.count) / Double(ProfileCategory.allCases.count)
        let confidence = profile.traits.isEmpty
            ? 0
            : profile.traits.map(\.confidence).reduce(0, +) / Double(profile.traits.count)
        let examplesBonus = min(Double(profile.examples.count), 5) / 5 * 0.1
        return min(1, categoriesCovered * 0.55 + confidence * 0.35 + examplesBonus)
    }

    func refresh() async { await profileStore.refresh() }
    func save(_ trait: ProfileTrait) async { await profileStore.save(trait) }
    func delete(_ trait: ProfileTrait) async { await profileStore.delete(trait) }
    func add(category: ProfileCategory, value: String) async { await profileStore.addTrait(category: category, value: value) }
    func deleteExample(_ example: WritingExample) async { await profileStore.deleteExample(example) }

    func deleteAll() async {
        if await profileStore.deleteAll() { Haptics.warning() }
    }

    func prepareExport() async {
        isExporting = true
        defer { isExporting = false }
        if let json = await profileStore.exportJSON() {
            export = ExportedProfile(json: json)
        }
    }
}
