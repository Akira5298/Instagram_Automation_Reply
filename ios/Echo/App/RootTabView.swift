import SwiftUI

struct RootTabView: View {
    @Bindable var app: AppModel
    @Environment(\.scenePhase) private var scenePhase
    /// Set when the scene really went to the background (not just inactive).
    @State private var wasInBackground = false

    var body: some View {
        TabView(selection: $app.selectedTab) {
            ChatView(viewModel: app.chatViewModel) { app.selectedTab = .scheduler }
                .tabItem { Label(AppTab.chat.title, systemImage: AppTab.chat.systemImage) }
                .tag(AppTab.chat)

            MessagesView(viewModel: app.messagesViewModel, settings: $app.settings) { app.setAutomationPaused($0) }
                .tabItem { Label(AppTab.messages.title, systemImage: AppTab.messages.systemImage) }
                .tag(AppTab.messages)
                .badge(app.messagingStore.conversations.reduce(0) { $0 + $1.unreadCount })

            SchedulerView(viewModel: app.schedulerViewModel)
                .tabItem { Label(AppTab.scheduler.title, systemImage: AppTab.scheduler.systemImage) }
                .tag(AppTab.scheduler)
                .badge(app.schedulerViewModel.issueCount)

            PersonalityView(viewModel: app.personalityViewModel)
                .tabItem { Label(AppTab.personality.title, systemImage: AppTab.personality.systemImage) }
                .tag(AppTab.personality)

            SettingsView(app: app)
                .tabItem { Label(AppTab.settings.title, systemImage: AppTab.settings.systemImage) }
                .tag(AppTab.settings)
        }
        .tint(EchoColor.cyan)
        .onChange(of: app.selectedTab) { Haptics.select() }
        .task { await app.refreshAll() }
        // One background-work task while ECHO is in the foreground: the live-update
        // long poll, plus the optional "Your day" upload every 15 minutes. Keyed on
        // "not in the background" rather than "active", because permission prompts,
        // Face ID, the Instagram login sheet and Control Center only make the scene
        // *inactive* for a moment; restarting everything for those caused a burst
        // of duplicate reloads. It restarts when the backend changes.
        .task(id: LiveKey(foreground: scenePhase != .background, store: ObjectIdentifier(app.messagingStore))) {
            guard scenePhase != .background else { return }
            let store = app.messagingStore
            await withTaskGroup(of: Void.self) { group in
                group.addTask { await store.runLiveUpdates() }
                group.addTask { await Self.contextLoop(store: store) }
            }
        }
        .onChange(of: scenePhase) { _, phase in
            // Catch up only after a real trip to the background (background →
            // inactive → active), not after every brief inactive moment.
            switch phase {
            case .background:
                wasInBackground = true
            case .active where wasInBackground:
                wasInBackground = false
                Task { await app.messagingStore.refresh() }
            default:
                break
            }
        }
        // Push (Phase 6): register the device with your server, open tapped notifications.
        .task { await PushManager.shared.refreshStatus() }
        .task(id: PushTokenKey(token: PushManager.shared.deviceToken, store: ObjectIdentifier(app.messagingStore))) {
            if let token = PushManager.shared.deviceToken {
                await app.messagingStore.registerDevice(token: token)
            }
        }
        .onChange(of: PushManager.shared.route) { _, route in
            guard let route else { return }
            openRoute(route)
            PushManager.shared.route = nil
        }
    }
}

extension RootTabView {
    @MainActor
    private static func contextLoop(store: MessagingStore) async {
        while !Task.isCancelled {
            let context = ContextManager.shared
            if context.settings.shareLocation || context.settings.shareCalendar {
                await context.sync(to: store)
            }
            try? await Task.sleep(for: .seconds(ContextManager.uploadInterval))
        }
    }

    /// Opens a tapped notification. Navigation changes are made one at a time
    /// and only when they change something: switching tab, popping the stack
    /// and presenting a sheet in the same frame makes SwiftUI's navigation
    /// update several times per frame (and can drop the sheet).
    private func openRoute(_ route: PushRoute) {
        let messages = app.messagesViewModel
        Task { @MainActor in
            switch route {
            case .conversation(let id):
                if messages.showingReviews {
                    messages.showingReviews = false
                    try? await Task.sleep(for: .milliseconds(400))   // let the sheet finish dismissing
                }
                if app.selectedTab != .messages { app.selectedTab = .messages }
                if messages.path != [id] { messages.path = [id] }
            case .reviews:
                if app.selectedTab != .messages { app.selectedTab = .messages }
                if !messages.path.isEmpty {
                    messages.path = []
                    try? await Task.sleep(for: .milliseconds(400))   // let the pop finish first
                }
                if !messages.showingReviews { messages.showingReviews = true }
            case .activity:
                if app.selectedTab != .scheduler { app.selectedTab = .scheduler }
            }
            await app.messagingStore.refresh()
        }
    }
}

private struct PushTokenKey: Hashable {
    let token: String?
    let store: ObjectIdentifier
}

private struct LiveKey: Hashable {
    let foreground: Bool
    let store: ObjectIdentifier
}

#Preview {
    RootTabView(app: AppModel())
        .preferredColorScheme(.dark)
}
