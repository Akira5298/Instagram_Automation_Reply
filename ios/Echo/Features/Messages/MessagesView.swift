import SwiftUI

struct MessagesView: View {
    @Bindable var viewModel: MessagesViewModel
    @Binding var settings: AppSettings
    var onTogglePause: (Bool) -> Void = { _ in }

    var body: some View {
        NavigationStack {
            ZStack {
                EchoBackground()
                ScrollView {
                    VStack(alignment: .leading, spacing: EchoSpacing.md) {
                        AutomationBanner(settings: $settings, automatedCount: viewModel.automatedCount, onTogglePause: onTogglePause)

                        if viewModel.isMock {
                            Label("Offline sample conversations — connect your backend in Settings", systemImage: "testtube.2")
                                .echoReadout(EchoColor.warning)
                        } else {
                            Label("Mock Instagram on your server — real Instagram connects in Phase 4", systemImage: "server.rack")
                                .echoReadout(EchoColor.cyan)
                        }

                        LazyVStack(spacing: EchoSpacing.sm) {
                            ForEach(viewModel.conversations) { conversation in
                                NavigationLink(value: conversation.id) {
                                    ConversationRow(
                                        conversation: conversation,
                                        automation: conversation.automationState(globalPause: settings.automationPaused)
                                    )
                                }
                                .buttonStyle(.plain)
                            }
                        }

                        if viewModel.conversations.isEmpty {
                            ContentUnavailableView.search(text: viewModel.searchText)
                        }
                    }
                    .padding(.horizontal, EchoSpacing.md)
                    .padding(.bottom, EchoSpacing.lg)
                }
            }
            .navigationTitle("Messages")
            .searchable(text: $viewModel.searchText, prompt: "Search friends")
            .refreshable { await viewModel.store.refresh() }
            .alert(
                "Messages",
                isPresented: Binding(
                    get: { viewModel.store.errorMessage != nil },
                    set: { if !$0 { viewModel.store.errorMessage = nil } }
                )
            ) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(viewModel.store.errorMessage ?? "")
            }
            .navigationDestination(for: UUID.self) { id in
                ConversationDetailView(store: viewModel.store, conversationID: id, globalPause: settings.automationPaused)
            }
        }
    }
}

/// Global emergency stop, visible right where automation happens.
struct AutomationBanner: View {
    @Binding var settings: AppSettings
    var onTogglePause: (Bool) -> Void = { _ in }
    let automatedCount: Int

    var body: some View {
        HStack(spacing: EchoSpacing.sm) {
            Image(systemName: settings.automationPaused ? "pause.circle.fill" : "bolt.circle.fill")
                .font(.title2)
                .foregroundStyle(settings.automationPaused ? EchoColor.danger : EchoColor.cyan)
                .symbolEffect(.pulse, isActive: !settings.automationPaused)
            VStack(alignment: .leading, spacing: 2) {
                Text(settings.automationPaused ? "Automation paused" : "Automation on")
                    .font(EchoFont.headline)
                    .foregroundStyle(EchoColor.textPrimary)
                Text(settings.automationPaused
                     ? "ECHO won't send anything until you resume."
                     : "\(automatedCount) friends have AI replies enabled.")
                    .font(EchoFont.caption)
                    .foregroundStyle(EchoColor.textSecondary)
            }
            Spacer()
            Button(settings.automationPaused ? "Resume" : "Pause all") {
                onTogglePause(!settings.automationPaused)
            }
            .buttonStyle(.neon(settings.automationPaused ? .secondary : .destructive, fullWidth: false))
            .controlSize(.small)
        }
        .glassCard(glow: settings.automationPaused ? EchoColor.danger : EchoColor.cyan)
    }
}

struct ConversationRow: View {
    let conversation: ConversationSummary
    let automation: AutomationState

    var body: some View {
        HStack(spacing: EchoSpacing.sm) {
            AvatarView(name: conversation.friend.displayName, active: conversation.unreadCount > 0)

            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(conversation.friend.displayName)
                        .font(EchoFont.headline)
                        .foregroundStyle(EchoColor.textPrimary)
                    Spacer()
                    if let last = conversation.lastMessage {
                        Text(last.sentAt, format: .relative(presentation: .named))
                            .font(EchoFont.caption)
                            .foregroundStyle(EchoColor.textTertiary)
                    }
                }
                Text(conversation.lastMessage?.text ?? "")
                    .font(EchoFont.callout)
                    .foregroundStyle(conversation.unreadCount > 0 ? EchoColor.textPrimary : EchoColor.textSecondary)
                    .lineLimit(1)
                HStack(spacing: EchoSpacing.xs) {
                    StatusPill(automation: automation)
                    Text(MessagesViewModel.windowText(for: conversation.friend))
                        .echoReadout()
                        .lineLimit(1)
                }
            }
        }
        .glassCard(glow: conversation.unreadCount > 0 ? EchoColor.neonPurple : nil, padding: EchoSpacing.sm)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens conversation and friend settings")
    }
}

#Preview {
    MessagesView(
        viewModel: MessagesViewModel(store: .mock()),
        settings: .constant(AppSettings())
    )
    .preferredColorScheme(.dark)
}
