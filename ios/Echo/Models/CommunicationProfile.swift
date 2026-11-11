import Foundation

/// Raw values match the backend's `profile_traits.category`.
enum ProfileCategory: String, Codable, CaseIterable, Identifiable, Sendable {
    case languages
    case vocabulary
    case tone
    case slang
    case emoji
    case humour
    case sentenceStyle = "sentence_style"
    case social
    case interests
    case friendPreferences = "friend_preferences"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .languages: return "Languages"
        case .vocabulary: return "Vocabulary & expressions"
        case .tone: return "Tone & formality"
        case .slang: return "Slang & abbreviations"
        case .emoji: return "Emoji"
        case .humour: return "Humour"
        case .sentenceStyle: return "Sentence length & punctuation"
        case .social: return "Social style"
        case .interests: return "Interests"
        case .friendPreferences: return "Friend preferences"
        }
    }

    var systemImage: String {
        switch self {
        case .languages: return "globe"
        case .vocabulary: return "textformat.abc"
        case .tone: return "slider.horizontal.3"
        case .slang: return "bubble.left"
        case .emoji: return "face.smiling"
        case .humour: return "theatermasks"
        case .sentenceStyle: return "text.alignleft"
        case .social: return "person.2"
        case .interests: return "star"
        case .friendPreferences: return "heart"
        }
    }
}

/// A single learned or stated fact about how you communicate.
struct ProfileTrait: Identifiable, Codable, Hashable, Sendable {
    enum Source: String, Codable, Sendable {
        case userStated = "user_stated"
        case inferred
        case userEdited = "user_edited"

        var label: String {
            switch self {
            case .userStated: return "Stated"
            case .inferred: return "Inferred"
            case .userEdited: return "Edited"
            }
        }
    }

    let id: UUID
    var category: ProfileCategory
    var key: String
    var value: String
    var source: Source
    /// 0...1. Stated or edited traits are 1.0.
    var confidence: Double
    var evidenceCount: Int
    var updatedAt: Date

    init(
        id: UUID = UUID(),
        category: ProfileCategory,
        key: String,
        value: String,
        source: Source,
        confidence: Double,
        evidenceCount: Int = 1,
        updatedAt: Date = .now
    ) {
        self.id = id
        self.category = category
        self.key = key
        self.value = value
        self.source = source
        self.confidence = confidence
        self.evidenceCount = evidenceCount
        self.updatedAt = updatedAt
    }

    /// Inferred traits stay "tentative" until there is enough evidence.
    var isTentative: Bool { source == .inferred && confidence < 0.7 }
}

/// A message you approved as typical of how you write. Used as a style
/// reference when ECHO drafts messages for you.
struct WritingExample: Identifiable, Codable, Hashable, Sendable {
    let id: UUID
    var text: String
    var language: String?
    var createdAt: Date

    init(id: UUID = UUID(), text: String, language: String? = nil, createdAt: Date = .now) {
        self.id = id
        self.text = text
        self.language = language
        self.createdAt = createdAt
    }
}

struct CommunicationProfile: Codable, Sendable, Equatable {
    var traits: [ProfileTrait] = []
    var examples: [WritingExample] = []
    /// Keys of traits you deleted — never re-learned automatically.
    var rejectedKeys: Set<String> = []

    init(traits: [ProfileTrait] = [], examples: [WritingExample] = [], rejectedKeys: Set<String> = []) {
        self.traits = traits
        self.examples = examples
        self.rejectedKeys = rejectedKeys
    }

    /// Tolerant decoding: missing arrays (older caches) become empty.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        traits = try c.decodeIfPresent([ProfileTrait].self, forKey: .traits) ?? []
        examples = try c.decodeIfPresent([WritingExample].self, forKey: .examples) ?? []
        rejectedKeys = try c.decodeIfPresent(Set<String>.self, forKey: .rejectedKeys) ?? []
    }

    func traits(in category: ProfileCategory) -> [ProfileTrait] {
        traits.filter { $0.category == category }
            .sorted { $0.confidence > $1.confidence }
    }
}
