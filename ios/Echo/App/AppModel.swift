import Foundation
import Observation

enum AppTab: String, CaseIterable, Identifiable {
    case chat, messages, scheduler, personality, settings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .chat: return "AI Chat"
        case .messages: return "Messages"
        case .scheduler: return "Scheduler"
        case .personality: return "Personality"
        case .settings: return "Settings"
        }
    }

    var systemImage: String {
        switch self {
        case .chat: return "sparkles"
        case .messages: return "paperplane.fill"
        case .scheduler: return "calendar.badge.clock"
        case .personality: return "brain.head.profile"
        case .settings: return "gearshape.fill"
        }
    }
}

/// How the AI chat + profile are served right now.
enum ConnectionMode: Equatable {
    /// Offline, on-device mock AI.
    case mock
    /// Your FastAPI backend (which calls Claude).
    case backend(URL)
    /// Backend selected but not usable yet; falls back to mock.
    case misconfigured(String)

    var isBackend: Bool {
        if case .backend = self { return true }
        return false
    }
}

struct ConnectionCheck: Identifiable {
    let id = UUID()
    let title: String
    let detail: String
    let ok: Bool
}

/// Composition root: creates stores and view models, and swaps the AI chat +
/// profile services between mock and your backend.
@MainActor
@Observable
final class AppModel {
    var selectedTab: AppTab = .chat
    var settings: AppSettings {
        didSet { try? settingsStore.save(settings) }
    }

    private(set) var connectionMode: ConnectionMode
    private(set) var hasBackendToken: Bool
    private(set) var profileStore: ProfileStore
    private(set) var chatViewModel: ChatViewModel
    private(set) var personalityViewModel: PersonalityViewModel

    private(set) var messagingStore: MessagingStore
    private(set) var messagesViewModel: MessagesViewModel
    private(set) var schedulerViewModel: SchedulerViewModel

    private let settingsStore: LocalJSONStore<AppSettings>
    private let session: URLSession

    /// Pass nil to use the defaults.
    init(
        settingsStore: LocalJSONStore<AppSettings>? = nil,
        session: URLSession = .shared
    ) {
        let settingsStore = settingsStore ?? LocalJSONStore(filename: "settings.json")
        let loadedSettings = settingsStore.load() ?? AppSettings()
        let token = KeychainStore.read(KeychainStore.backendTokenAccount)

        let mode = Self.resolveMode(settings: loadedSettings, token: token)
        let services = Self.makeServices(mode: mode, token: token, session: session)

        self.settingsStore = settingsStore
        self.session = session
        self.settings = loadedSettings
        self.hasBackendToken = token != nil
        self.connectionMode = mode
        self.profileStore = services.profileStore
        self.chatViewModel = services.chatViewModel
        self.personalityViewModel = PersonalityViewModel(profileStore: services.profileStore)
        self.messagingStore = services.messagingStore
        self.messagesViewModel = MessagesViewModel(store: services.messagingStore)
        self.schedulerViewModel = SchedulerViewModel(store: services.messagingStore, timeZoneID: loadedSettings.defaultTimeZoneID)
        wireCallbacks()
    }

    /// Chat-created jobs show up in the Scheduler right away.
    private func wireCallbacks() {
        chatViewModel.onActions = { [weak self] _ in
            Task { await self?.messagingStore.refreshJobs() }
        }
    }

    var isUsingMockAI: Bool { !connectionMode.isBackend }

    // MARK: - Connection

    /// Rebuilds the chat + profile services after a settings change and loads fresh data.
    func applyConnectionSettings() {
        let token = KeychainStore.read(KeychainStore.backendTokenAccount)
        hasBackendToken = token != nil
        let mode = Self.resolveMode(settings: settings, token: token)
        let services = Self.makeServices(mode: mode, token: token, session: session)
        connectionMode = mode
        profileStore = services.profileStore
        chatViewModel = services.chatViewModel
        personalityViewModel = PersonalityViewModel(profileStore: services.profileStore)
        messagingStore = services.messagingStore
        messagesViewModel = MessagesViewModel(store: services.messagingStore)
        schedulerViewModel = SchedulerViewModel(store: services.messagingStore, timeZoneID: settings.defaultTimeZoneID)
        wireCallbacks()
        Task { await refreshAll() }
    }

    func refreshAll() async {
        await profileStore.refresh()
        await chatViewModel.load()
        await messagingStore.refresh()
        // The server's emergency-stop flag is the source of truth.
        if let paused = await messagingStore.loadAutomationPaused(), paused != settings.automationPaused {
            settings.automationPaused = paused
            settings.automationPausedAt = paused ? .now : nil
        }
    }

    /// Backend + mock Instagram: create the sample account and friends.
    func loadSampleFriends() async {
        await messagingStore.seedSampleData()
    }

    func saveBackendToken(_ token: String) throws {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        try KeychainStore.save(trimmed, account: KeychainStore.backendTokenAccount)
        applyConnectionSettings()
    }

    func removeBackendToken() {
        KeychainStore.delete(KeychainStore.backendTokenAccount)
        applyConnectionSettings()
    }

    /// Checks the server is reachable, then that the token is accepted.
    func testConnection() async -> [ConnectionCheck] {
        guard let baseURL = Self.normalisedURL(settings.backendBaseURL) else {
            return [ConnectionCheck(title: "Backend URL", detail: "Enter a full URL like http://localhost:8000", ok: false)]
        }
        var checks: [ConnectionCheck] = []

        struct Health: Decodable {
            let status: String
            let database: String
            let aiProvider: String
            let chatModel: String?
        }
        do {
            let data = try await APIClient(baseURL: baseURL, token: "", session: session).send("GET", "health")
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            let health = try decoder.decode(Health.self, from: data)
            checks.append(ConnectionCheck(title: "Server", detail: "Reachable", ok: true))
            checks.append(ConnectionCheck(title: "Database", detail: health.database, ok: health.database == "ok"))
            checks.append(ConnectionCheck(
                title: "AI",
                detail: health.aiProvider == "anthropic" ? (health.chatModel ?? "Claude") : "Mock provider",
                ok: true
            ))
        } catch {
            return [ConnectionCheck(title: "Server", detail: error.localizedDescription, ok: false)]
        }

        guard let token = KeychainStore.read(KeychainStore.backendTokenAccount) else {
            checks.append(ConnectionCheck(title: "API token", detail: "Not saved yet", ok: false))
            return checks
        }
        do {
            _ = try await APIClient(baseURL: baseURL, token: token, session: session).send("GET", "v1/profile")
            checks.append(ConnectionCheck(title: "API token", detail: "Accepted", ok: true))
        } catch {
            checks.append(ConnectionCheck(title: "API token", detail: error.localizedDescription, ok: false))
        }
        return checks
    }

    // MARK: - Automation & data

    func setAutomationPaused(_ paused: Bool) {
        settings.automationPaused = paused
        settings.automationPausedAt = paused ? .now : nil
        Haptics.warning()
        // The worker checks the server-side flag before every send.
        Task { await messagingStore.setAutomationPaused(paused) }
    }

    /// Removes ECHO's data from this iPhone only (caches, settings, token).
    /// Server data is managed from the Personality and AI Chat screens.
    func deleteAllLocalData() {
        chatViewModel.resetLocalCache()
        for file in ["chat_history.json", "chat_history_remote.json", "profile.json", "profile-remote-cache.json"] {
            LocalJSONStore<CommunicationProfile>(filename: file).delete()
        }
        KeychainStore.delete(KeychainStore.backendTokenAccount)
        settingsStore.delete()
        settings = AppSettings()
        applyConnectionSettings()
    }

    // MARK: - Factories

    static func normalisedURL(_ raw: String) -> URL? {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        while text.hasSuffix("/") { text.removeLast() }
        guard let url = URL(string: text),
              let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
              url.host != nil else { return nil }
        return url
    }

    static func resolveMode(settings: AppSettings, token: String?) -> ConnectionMode {
        guard !settings.useMockServices else { return .mock }
        guard let url = normalisedURL(settings.backendBaseURL) else {
            return .misconfigured("The backend URL isn't valid.")
        }
        guard token != nil else { return .misconfigured("Save your API token to connect.") }
        return .backend(url)
    }

    private static func makeServices(
        mode: ConnectionMode, token: String?, session: URLSession
    ) -> (profileStore: ProfileStore, chatViewModel: ChatViewModel, messagingStore: MessagingStore) {
        if case .backend(let url) = mode, let token {
            let api = APIClient(baseURL: url, token: token, session: session)
            let repository = RemoteProfileRepository(api: api)
            let profileStore = ProfileStore(repository: repository, initial: repository.cachedProfile ?? CommunicationProfile())
            let chat = ChatViewModel(
                service: BackendChatService(api: api),
                profileStore: profileStore,
                store: LocalJSONStore(filename: "chat_history_remote.json")
            )
            return (profileStore, chat, MessagingStore(backend: RemoteMessagingBackend(api: api)))
        }
        let repository = LocalProfileRepository()
        let profileStore = ProfileStore(repository: repository, initial: repository.profile)
        let chat = ChatViewModel(service: MockChatService(), profileStore: profileStore)
        return (profileStore, chat, MessagingStore.mock())
    }
}
