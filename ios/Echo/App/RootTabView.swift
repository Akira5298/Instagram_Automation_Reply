import SwiftUI

struct RootTabView: View {
    @Bindable var app: AppModel
    @Environment(\.scenePhase) private var scenePhase

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
        // Live updates while the app is open; restarts when the backend changes.
        .task(id: LiveKey(active: scenePhase == .active, store: ObjectIdentifier(app.messagingStore))) {
            guard scenePhase == .active else { return }
            await app.messagingStore.runLiveUpdates()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await app.messagingStore.refresh() } }   // catch up after background
        }
        // "Your day" context (only if you turned it on): on open, then every 15 minutes.
        .task(id: LiveKey(active: scenePhase == .active, store: ObjectIdentifier(app.messagingStore))) {
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                let context = ContextManager.shared
                if context.settings.shareLocation || context.settings.shareCalendar {
                    await context.sync(to: app.messagingStore)
                }
                try? await Task.sleep(for: .seconds(ContextManager.uploadInterval))
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
    private func openRoute(_ route: PushRoute) {
        switch route {
        case .conversation(let id):
            app.selectedTab = .messages
            app.messagesViewModel.path = [id]
        case .reviews:
            app.selectedTab = .messages
            app.messagesViewModel.path = []
            app.messagesViewModel.showingReviews = true
        case .activity:
            app.selectedTab = .scheduler
        }
        Task { await app.messagingStore.refresh() }
    }
}

private struct PushTokenKey: Hashable {
    let token: String?
    let store: ObjectIdentifier
}

private struct LiveKey: Hashable {
    let active: Bool
    let store: ObjectIdentifier
}

#Preview {
    RootTabView(app: AppModel())
        .preferredColorScheme(.dark)
}
