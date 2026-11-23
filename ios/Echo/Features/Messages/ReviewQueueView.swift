import SwiftUI

/// Drafts ECHO held back for you: edit, send, dismiss, or mark as answered.
/// Sending still goes through the server's Meta checks (24-hour window,
/// consent, connected account).
struct ReviewQueueView: View {
    let store: MessagingStore
    @Environment(\.dismiss) private var dismiss
    @State private var edits: [UUID: String] = [:]
    @State private var busy: Set<UUID> = []
    /// Which card's text field has the keyboard. Cleared before a card is sent
    /// (it disappears from the list) and before the sheet closes, so the keyboard
    /// never belongs to a view that's being removed.
    @FocusState private var focusedCard: UUID?

    var body: some View {
        NavigationStack {
            ZStack {
                EchoBackground()
                ScrollView {
                    LazyVStack(spacing: EchoSpacing.md) {
                        ForEach(store.reviews) { item in
                            ReviewCard(
                                item: item,
                                text: Binding(
                                    get: { edits[item.id] ?? item.draft ?? "" },
                                    set: { edits[item.id] = $0 }
                                ),
                                busy: busy.contains(item.id),
                                onAction: { action in resolve(item, action) },
                                onPick: { suggestion in send(item, suggestion) },
                                onMore: { more(item) },
                                focus: $focusedCard
                            )
                        }
                        if store.reviews.isEmpty {
                            ContentUnavailableView(
                                "Nothing waiting",
                                systemImage: "checkmark.bubble",
                                description: Text("When ECHO holds a reply for you — money, plans, anything only you can answer — it shows up here.")
                            )
                            .padding(.top, EchoSpacing.lg)
                        }
                    }
                    .padding(EchoSpacing.md)
                }
                .scrollDismissesKeyboard(.interactively)
            }
            .navigationTitle("Needs you")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        focusedCard = nil
                        dismiss()
                    }
                }
            }
            .refreshable { await store.refreshReviews() }
            .task { await store.refreshReviews() }
        }
    }

    /// One tap on a suggestion sends it as is (words, or a ❤️ reaction).
    private func send(_ item: ReviewItem, _ suggestion: ReviewItem.Suggestion) {
        focusedCard = nil
        busy.insert(item.id)
        Task {
            let ok = await store.resolveReview(item, action: .send,
                                               text: suggestion.kind == .text ? suggestion.text : nil,
                                               kind: suggestion.kind)
            busy.remove(item.id)
            if ok { edits[item.id] = nil; Haptics.success() } else { Haptics.warning() }
        }
    }

    private func more(_ item: ReviewItem) {
        busy.insert(item.id)
        Task {
            await store.moreSuggestions(for: item)
            busy.remove(item.id)
        }
    }

    private func resolve(_ item: ReviewItem, _ action: ReviewItem.Action) {
        focusedCard = nil
        busy.insert(item.id)
        let edited = edits[item.id]
        let text = action == .send && edited != nil && edited != item.draft ? edited : nil
        Task {
            let ok = await store.resolveReview(item, action: action, text: text)
            busy.remove(item.id)
            if ok {
                edits[item.id] = nil
                if action == .send { Haptics.success() } else { Haptics.tap() }
            } else {
                Haptics.warning()
            }
        }
    }
}

struct ReviewCard: View {
    @ViewBuilder
    private func focusable(_ field: TextField<Text>) -> some View {
        if let focus {
            field.focused(focus, equals: item.id)
        } else {
            field
        }
    }

    let item: ReviewItem
    @Binding var text: String
    let busy: Bool
    let onAction: (ReviewItem.Action) -> Void
    var onPick: (ReviewItem.Suggestion) -> Void = { _ in }
    var onMore: () -> Void = {}
    var focus: FocusState<UUID?>.Binding? = nil

    private var windowOpen: Bool { item.windowOpen() }
    private var canSend: Bool { !busy && (item.origin != .autoReply || windowOpen) }

    var body: some View {
        VStack(alignment: .leading, spacing: EchoSpacing.sm) {
            HStack(alignment: .firstTextBaseline) {
                AvatarView(name: item.friendName, size: 32, active: true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.friendName).font(EchoFont.headline)
                    Text(item.origin == .autoReply ? "Reply" : "Scheduled message")
                        .echoReadout()
                }
                Spacer()
                if let expires = item.windowExpiresAt, windowOpen {
                    Text("Window ") + Text(expires, style: .relative)
                } else if item.origin == .autoReply {
                    Text("Window closed").foregroundStyle(EchoColor.danger)
                }
            }
            .font(EchoFont.caption)
            .foregroundStyle(EchoColor.textSecondary)

            if let inbound = item.inboundText, !inbound.isEmpty {
                Text(inbound)
                    .font(EchoFont.body)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(EchoColor.surfaceElevated, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .accessibilityLabel("\(item.friendName) wrote: \(inbound)")
            }

            Label("Held because it \(item.reasonText)", systemImage: item.systemImage)
                .font(EchoFont.caption)
                .foregroundStyle(EchoColor.warning)

            if !item.options.isEmpty {
                Text("Tap to send").echoReadout()
                VStack(alignment: .leading, spacing: EchoSpacing.xs) {
                    ForEach(item.options) { option in
                        SuggestionChip(option: option) { onPick(option) }
                            .disabled(!canSend)
                            .contextMenu {
                                if option.kind == .text {
                                    Button("Edit before sending", systemImage: "pencil") { text = option.text }
                                }
                            }
                    }
                    if item.origin == .autoReply {
                        HStack {
                            if !item.options.contains(where: { $0.kind == .reaction }) {
                                SuggestionChip(option: .init(text: "❤️", kind: .reaction)) {
                                    onPick(.init(text: "❤️", kind: .reaction))
                                }
                                .disabled(!canSend)
                            }
                            Button {
                                onMore()
                            } label: {
                                Label("More options", systemImage: "sparkles")
                                    .font(EchoFont.caption)
                            }
                            .buttonStyle(.plain)
                            .foregroundStyle(EchoColor.cyan)
                            .disabled(busy)
                        }
                    }
                }
                Text("Or write your own").echoReadout()
            }

            focusable(TextField("Your reply", text: $text, axis: .vertical))
                .lineLimit(2...6)
                .font(EchoFont.body)
                .padding(10)
                .background(EchoColor.surface.opacity(0.9), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(EchoColor.neonPurple.opacity(0.4)))
                .accessibilityLabel("Draft reply to \(item.friendName). Edit before sending.")

            if text.contains("[") {
                Label("Fill in the [placeholders] before sending.", systemImage: "pencil.and.list.clipboard")
                    .font(EchoFont.caption)
                    .foregroundStyle(EchoColor.textTertiary)
            }

            HStack(spacing: EchoSpacing.xs) {
                Button {
                    onAction(.send)
                } label: {
                    if busy { ProgressView() } else { Label("Send", systemImage: "paperplane.fill") }
                }
                .buttonStyle(.neon(.primary))
                .disabled(busy || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                          || (item.origin == .autoReply && !windowOpen))

                Menu {
                    Button("I replied in Instagram", systemImage: "checkmark.message") { onAction(.sentManually) }
                    Button("Dismiss", systemImage: "xmark", role: .destructive) { onAction(.dismiss) }
                } label: {
                    Image(systemName: "ellipsis")
                        .frame(width: 44, height: 44)
                        .background(EchoColor.surfaceElevated, in: Circle())
                }
                .disabled(busy)
                .accessibilityLabel("More options")
            }
        }
        .foregroundStyle(EchoColor.textPrimary)
        .glassCard(glow: EchoColor.warning)
    }
}

/// A tap-to-send suggestion. Long-press a text one to edit it first.
struct SuggestionChip: View {
    let option: ReviewItem.Suggestion
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if option.kind == .reaction {
                    Text("❤️")
                    Text("React").font(EchoFont.caption)
                } else {
                    Text(option.text).multilineTextAlignment(.leading)
                    Spacer(minLength: 0)
                    Image(systemName: "paperplane").font(.caption)
                }
            }
            .font(EchoFont.body)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: option.kind == .reaction ? nil : .infinity, alignment: .leading)
            .background(EchoColor.neonPurple.opacity(0.16), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(EchoColor.neonPurple.opacity(0.45)))
        }
        .buttonStyle(.plain)
        .foregroundStyle(EchoColor.textPrimary)
        .accessibilityLabel(option.kind == .reaction ? "React with a heart" : "Send: \(option.text)")
        .accessibilityHint(option.kind == .text ? "Long press to edit first" : "")
    }
}

/// "N replies need you" — shown above the conversation list.
struct ReviewBanner: View {
    let count: Int
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: EchoSpacing.sm) {
                Image(systemName: "exclamationmark.bubble.fill")
                    .font(.title2)
                    .foregroundStyle(EchoColor.warning)
                VStack(alignment: .leading, spacing: 2) {
                    Text(count == 1 ? "1 reply needs you" : "\(count) replies need you")
                        .font(EchoFont.headline)
                        .foregroundStyle(EchoColor.textPrimary)
                    Text("ECHO drafted them but wants your OK first.")
                        .font(EchoFont.caption)
                        .foregroundStyle(EchoColor.textSecondary)
                }
                Spacer()
                Image(systemName: "chevron.right").foregroundStyle(EchoColor.textTertiary)
            }
            .glassCard(glow: EchoColor.warning)
        }
        .buttonStyle(.plain)
        .accessibilityHint("Opens the review queue")
    }
}
