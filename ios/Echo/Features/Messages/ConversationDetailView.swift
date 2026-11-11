import SwiftUI

struct ConversationDetailView: View {
    let store: MessagingStore
    let conversationID: UUID
    let globalPause: Bool

    @State private var reply = ""
    @State private var showingSettings = false

    var body: some View {
        ZStack {
            EchoBackground()
            if let conversation = store.conversation(id: conversationID) {
                content(conversation)
            } else {
                ContentUnavailableView("Conversation not found", systemImage: "bubble.left.and.exclamationmark.bubble.right")
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { store.markRead(conversationID) }
    }

    @ViewBuilder
    private func content(_ conversation: ConversationSummary) -> some View {
        let friend = conversation.friend
        ScrollView {
            VStack(spacing: EchoSpacing.md) {
                FriendControlsCard(
                    friend: friend,
                    automation: conversation.automationState(globalPause: globalPause),
                    onChange: { change in store.updateFriend(in: conversationID, change) }
                )

                LazyVStack(spacing: EchoSpacing.xs) {
                    ForEach(conversation.messages.sorted { $0.sentAt < $1.sentAt }) { message in
                        InstagramBubble(message: message)
                    }
                }
            }
            .padding(EchoSpacing.md)
        }
        .defaultScrollAnchor(.bottom)
        .safeAreaInset(edge: .bottom) {
            HStack(spacing: EchoSpacing.xs) {
                TextField("Reply yourself…", text: $reply, axis: .vertical)
                    .lineLimit(1...4)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(EchoColor.surfaceElevated.opacity(0.85), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                Button {
                    let text = reply
                    reply = ""
                    Haptics.tap()
                    Task {
                        let sent = await store.sendManualReply(text, in: conversationID)
                        if !sent { reply = text }
                    }
                } label: {
                    Image(systemName: "paperplane.fill")
                        .frame(width: 44, height: 44)
                        .foregroundStyle(.white)
                        .background(EchoColor.accentGradient, in: Circle())
                }
                .disabled(reply.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityLabel("Send manual reply")
            }
            .padding(.horizontal, EchoSpacing.md)
            .padding(.vertical, EchoSpacing.xs)
            .background(.ultraThinMaterial)
        }
        .navigationTitle(friend.displayName)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Menu {
                    Button("Simulate a message from \(friend.firstName)", systemImage: "arrow.down.message") {
                        Task { await store.simulateInbound(simulatedText(for: friend), in: conversationID) }
                    }
                } label: {
                    Image(systemName: "testtube.2")
                }
                .accessibilityLabel("Testing tools")
            }
        }
    }
}

private func simulatedText(for friend: Friend) -> String {
    switch friend.language {
    case "ja": return "ねえ、今週末ひま？"
    case "it": return "Ciao! Sei libero questo weekend?"
    case "zh-Hans": return "这个周末有空吗？"
    default: return "hey, free this weekend?"
    }
}

/// Per-friend automation controls (Feature 5).
struct FriendControlsCard: View {
    let friend: Friend
    let automation: AutomationState
    let onChange: ((inout Friend) -> Void) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: EchoSpacing.sm) {
            HStack {
                AvatarView(name: friend.displayName, size: 40, active: friend.aiReplyEnabled)
                VStack(alignment: .leading, spacing: 2) {
                    Text("@\(friend.username)").font(EchoFont.callout).foregroundStyle(EchoColor.textSecondary)
                    Text(MessagesViewModel.windowText(for: friend)).echoReadout()
                }
                Spacer()
                StatusPill(automation: automation)
            }

            Divider().overlay(EchoColor.hairline)

            Toggle(isOn: Binding(
                get: { friend.aiReplyEnabled },
                set: { newValue in
                    Haptics.select()
                    onChange { $0.aiReplyEnabled = newValue }
                }
            )) {
                Label("AI replies", systemImage: "bolt.fill")
            }
            .tint(EchoColor.neonPurple)

            Picker(selection: Binding(
                get: { friend.style },
                set: { newValue in onChange { $0.style = newValue } }
            )) {
                ForEach(Friend.StylePreset.allCases) { preset in
                    Text(preset.title).tag(preset)
                }
            } label: {
                Label("Writing style", systemImage: "paintbrush")
            }
            .tint(EchoColor.cyan)

            Text(friend.style.summary)
                .font(EchoFont.caption)
                .foregroundStyle(EchoColor.textTertiary)

            HStack {
                Label("Consent", systemImage: "checkmark.shield")
                Spacer()
                Menu {
                    Button("They agreed to AI-assisted messages", systemImage: "checkmark.seal") {
                        onChange { $0.consent = .granted }
                    }
                    Button("I've asked them", systemImage: "questionmark.bubble") {
                        onChange { $0.consent = .requested }
                    }
                    Button("They opted out", systemImage: "hand.raised", role: .destructive) {
                        onChange { $0.consent = .revoked }
                    }
                } label: {
                    HStack(spacing: 4) {
                        Text(friend.consent.title)
                        Image(systemName: "chevron.up.chevron.down").imageScale(.small)
                    }
                    .font(EchoFont.callout.weight(.semibold))
                    .foregroundStyle(friend.consent.tint)
                }
                .accessibilityLabel("Consent: \(friend.consent.title). Change")
            }

            if friend.aiReplyEnabled && friend.consent != .granted {
                Label(
                    "Automatic sending stays off until \(friend.displayName.components(separatedBy: " ").first ?? "they") agree to AI-assisted replies. ECHO will draft and ask you instead.",
                    systemImage: "info.circle"
                )
                .font(EchoFont.caption)
                .foregroundStyle(EchoColor.warning)
            }
        }
        .font(EchoFont.callout)
        .foregroundStyle(EchoColor.textPrimary)
        .glassCard(glow: friend.aiReplyEnabled ? EchoColor.neonPurple : nil)
    }
}

struct InstagramBubble: View {
    let message: InstagramMessage

    private var fromFriend: Bool { message.sender == .friend }

    var body: some View {
        VStack(alignment: fromFriend ? .leading : .trailing, spacing: 3) {
            Text(message.text)
                .font(EchoFont.body)
                .foregroundStyle(EchoColor.textPrimary)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .fill(fromFriend ? AnyShapeStyle(EchoColor.surfaceElevated) : AnyShapeStyle(EchoColor.userBubbleGradient))
                )
            HStack(spacing: 4) {
                if message.sender == .echoAuto || message.sender == .echoScheduled {
                    Image(systemName: "sparkles")
                    Text(message.sender == .echoAuto ? "Sent by ECHO" : "Scheduled by ECHO")
                }
                Text(message.sentAt, style: .time)
            }
            .font(EchoFont.monoSmall)
            .foregroundStyle(EchoColor.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: fromFriend ? .leading : .trailing)
        .padding(fromFriend ? .trailing : .leading, 48)
        .accessibilityElement(children: .combine)
    }
}
