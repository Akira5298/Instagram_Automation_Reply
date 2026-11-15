import SwiftUI

/// Drafts ECHO held back for you: edit, send, dismiss, or mark as answered.
/// Sending still goes through the server's Meta checks (24-hour window,
/// consent, connected account).
struct ReviewQueueView: View {
    let store: MessagingStore
    @Environment(\.dismiss) private var dismiss
    @State private var edits: [UUID: String] = [:]
    @State private var busy: Set<UUID> = []

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
                                onAction: { action in resolve(item, action) }
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
            }
            .navigationTitle("Needs you")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .refreshable { await store.refreshReviews() }
            .task { await store.refreshReviews() }
        }
    }

    private func resolve(_ item: ReviewItem, _ action: ReviewItem.Action) {
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
    let item: ReviewItem
    @Binding var text: String
    let busy: Bool
    let onAction: (ReviewItem.Action) -> Void

    private var windowOpen: Bool { item.windowOpen() }

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

            TextField("Your reply", text: $text, axis: .vertical)
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
