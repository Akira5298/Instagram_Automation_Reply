import SwiftUI

struct ConversationDetailView: View {
    let store: MessagingStore
    let conversationID: UUID
    let globalPause: Bool

    @State private var reply = ""
    @State private var showingSettings = false
    @State private var showingStyle = false
    @State private var showingMemories = false
    @State private var showingReviews = false
    @FocusState private var replyFocused: Bool

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
        // Bookkeeping only; markRead skips the write when it's already read, so
        // pushing this screen doesn't re-render the list underneath mid-transition.
        .onAppear { store.conversationOpened(conversationID) }
        .onDisappear {
            replyFocused = false
            store.conversationClosed(conversationID)
        }
    }

    /// Put the keyboard away before a sheet slides up, so UIKit isn't moving
    /// the keyboard and presenting a sheet in the same animation.
    private func present(_ flag: Binding<Bool>) {
        replyFocused = false
        flag.wrappedValue = true
    }

    @ViewBuilder
    private func content(_ conversation: ConversationSummary) -> some View {
        let friend = conversation.friend
        ScrollView {
            VStack(spacing: EchoSpacing.md) {
                FriendControlsCard(
                    friend: friend,
                    automation: conversation.automationState(globalPause: globalPause,
                                                             pendingReviews: store.pendingReviews(for: friend.id)),
                    onChange: { change in store.updateFriend(in: conversationID, change) },
                    pendingReviews: store.pendingReviews(for: friend.id),
                    onTakeOver: { takeOver in
                        Haptics.select()
                        Task { await store.setTakenOver(takeOver, in: conversationID) }
                    },
                    onRequestConsent: {
                        Task {
                            if await store.requestConsent(in: conversationID) { Haptics.success() }
                        }
                    },
                    onEditStyle: { present($showingStyle) },
                    onEditMemories: { present($showingMemories) },
                    onOpenReviews: { present($showingReviews) }
                )

                if friend.aiReplyEnabled, let outcome = conversation.lastAutoReply {
                    HStack(alignment: .firstTextBaseline, spacing: EchoSpacing.xs) {
                        Image(systemName: outcome.systemImage).foregroundStyle(outcome.tint)
                        Text(outcome.text).foregroundStyle(EchoColor.textSecondary)
                        Spacer(minLength: 0)
                        Text(outcome.at, style: .relative).foregroundStyle(EchoColor.textTertiary)
                    }
                    .font(EchoFont.caption)
                    .padding(.horizontal, EchoSpacing.xs)
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel("Latest automatic reply: \(outcome.text)")
                }

                LazyVStack(spacing: EchoSpacing.xs) {
                    ForEach(conversation.messages.sorted { $0.sentAt < $1.sentAt }) { message in
                        InstagramBubble(message: message)
                    }
                }
            }
            .padding(EchoSpacing.md)
        }
        .defaultScrollAnchor(.bottom)
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom) {
            HStack(spacing: EchoSpacing.xs) {
                TextField("Reply yourself…", text: $reply, axis: .vertical)
                    .lineLimit(1...4)
                    .focused($replyFocused)
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
        .sheet(isPresented: $showingStyle) {
            FriendStyleSheet(store: store, friend: friend)
                .presentationDetents([.large])
        }
        .sheet(isPresented: $showingReviews) {
            ReviewQueueView(store: store)
        }
        .sheet(isPresented: $showingMemories) {
            FriendMemoriesSheet(store: store, friend: friend)
        }
        .toolbar {
            if store.usesMockInstagram {
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
    var pendingReviews: Int = 0
    var onTakeOver: (Bool) -> Void = { _ in }
    var onRequestConsent: () -> Void = {}
    var onEditStyle: () -> Void = {}
    var onEditMemories: () -> Void = {}
    var onOpenReviews: () -> Void = {}

    @State private var confirmingConsentRequest = false

    private static let delays: [(Int, String)] = [(0, "Right away"), (30, "30 seconds"), (45, "45 seconds"),
                                                   (60, "1 minute"), (300, "5 minutes"), (900, "15 minutes")]

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

            if friend.consent == .unknown || friend.consent == .requested {
                Button {
                    confirmingConsentRequest = true
                } label: {
                    Label(friend.consent == .requested ? "Ask again by DM" : "Ask \(friend.firstName) by DM",
                          systemImage: "paperplane")
                }
                .buttonStyle(.neon(.secondary))
                .disabled(friend.standardWindowRemaining() == nil)
                .confirmationDialog("Send your consent request to \(friend.firstName)?",
                                    isPresented: $confirmingConsentRequest, titleVisibility: .visible) {
                    Button("Send request") { onRequestConsent() }
                } message: {
                    Text("They can reply YES to allow AI-assisted replies, or STOP anytime. ECHO records their answer automatically. You can edit the wording in Settings.")
                }
                if friend.standardWindowRemaining() == nil {
                    Text("You can ask next time \(friend.firstName) messages you (Instagram's 24-hour rule).")
                        .font(EchoFont.caption)
                        .foregroundStyle(EchoColor.textTertiary)
                }
            }

            if friend.aiReplyEnabled && friend.consent != .granted {
                Label(
                    "Automatic replies stay off until \(friend.firstName) agrees. ECHO won't reply to them meanwhile.",
                    systemImage: "info.circle"
                )
                .font(EchoFont.caption)
                .foregroundStyle(EchoColor.warning)
            }

            if pendingReviews > 0 {
                Button(action: onOpenReviews) {
                    Label(pendingReviews == 1 ? "1 draft waiting for you" : "\(pendingReviews) drafts waiting for you",
                          systemImage: "exclamationmark.bubble.fill")
                }
                .buttonStyle(.neon(.primary))
            }

            if friend.aiReplyEnabled {
                Divider().overlay(EchoColor.hairline)
                autoReplyControls
            }
        }
        .font(EchoFont.callout)
        .foregroundStyle(EchoColor.textPrimary)
        .glassCard(glow: friend.aiReplyEnabled ? EchoColor.neonPurple : nil)
    }
}

extension FriendControlsCard {
    @ViewBuilder
    var autoReplyControls: some View {
        if friend.isTakenOver(), let until = friend.manualOverrideUntil {
            HStack {
                Label {
                    Text("You're handling this chat until ") + Text(until, style: .time)
                } icon: {
                    Image(systemName: "person.fill.checkmark")
                }
                .font(EchoFont.caption)
                .foregroundStyle(EchoColor.blocked)
                Spacer()
                Button("Hand back") { onTakeOver(false) }
                    .buttonStyle(.neon(.secondary, fullWidth: false))
                    .controlSize(.small)
            }
        } else {
            Button {
                onTakeOver(true)
            } label: {
                Label("Take over this chat", systemImage: "hand.raised")
            }
            .buttonStyle(.neon(.secondary))
            .accessibilityHint("ECHO stops replying to \(friend.firstName) for a while")
        }

        Picker(selection: Binding(
            get: { friend.replyDelaySeconds ?? 45 },
            set: { newValue in onChange { $0.replyDelaySeconds = newValue } }
        )) {
            ForEach(Self.delays, id: \.0) { option in
                Text(option.1).tag(option.0)
            }
        } label: {
            Label("Reply after", systemImage: "timer")
        }
        .tint(EchoColor.cyan)

        Toggle(isOn: Binding(
            get: { friend.reviewFirst ?? false },
            set: { newValue in
                Haptics.select()
                onChange { $0.reviewFirst = newValue }
            }
        )) {
            Label("Check every reply with me", systemImage: "checkmark.bubble")
        }
        .tint(EchoColor.neonPurple)

        Button(action: onEditStyle) {
            Label("Reply style for \(friend.firstName)", systemImage: "slider.horizontal.3")
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .foregroundStyle(EchoColor.cyan)

        Button(action: onEditMemories) {
            Label("What ECHO remembers about \(friend.firstName)", systemImage: "brain.head.profile")
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .foregroundStyle(EchoColor.cyan)

        Text("ECHO waits a moment so several messages get one reply, and steps back for a while whenever you reply yourself. Money, plans, photos and anything only you can answer always come to you first.")
            .font(EchoFont.caption)
            .foregroundStyle(EchoColor.textTertiary)
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
