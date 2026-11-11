import SwiftUI

/// In-app activity feed: sent, blocked, failed and "needs your attention"
/// events. Push notifications for the same events arrive in Phase 6.
struct ActivitySheet: View {
    let store: MessagingStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if store.activity.isEmpty {
                    ContentUnavailableView(
                        "No activity yet",
                        systemImage: "bell.slash",
                        description: Text("Sent, blocked and failed messages show up here.")
                    )
                    .listRowBackground(Color.clear)
                }
                ForEach(store.activity) { item in
                    HStack(alignment: .top, spacing: EchoSpacing.sm) {
                        Image(systemName: item.systemImage)
                            .foregroundStyle(item.tint)
                            .frame(width: 24)
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                Text(item.title)
                                    .font(EchoFont.headline)
                                    .foregroundStyle(EchoColor.textPrimary)
                                if item.readAt == nil {
                                    Circle().fill(EchoColor.cyan).frame(width: 7, height: 7)
                                        .accessibilityLabel("Unread")
                                }
                            }
                            Text(item.body)
                                .font(EchoFont.callout)
                                .foregroundStyle(EchoColor.textSecondary)
                            Text(item.createdAt, format: .relative(presentation: .named))
                                .font(EchoFont.caption)
                                .foregroundStyle(EchoColor.textTertiary)
                        }
                    }
                    .listRowBackground(EchoColor.surface.opacity(0.6))
                    .accessibilityElement(children: .combine)
                }
            }
            .scrollContentBackground(.hidden)
            .background(EchoColor.background)
            .refreshable { await store.refreshJobs() }
            .navigationTitle("Activity")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .task {
                await store.refreshJobs()
                if store.unreadActivityCount > 0 {
                    try? await Task.sleep(for: .seconds(1.5))
                    await store.markActivityRead()
                }
            }
        }
    }
}
