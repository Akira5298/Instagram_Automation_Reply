import SwiftUI

struct RootTabView: View {
    @Bindable var app: AppModel

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
    }
}

#Preview {
    RootTabView(app: AppModel())
        .preferredColorScheme(.dark)
}
