import SwiftUI

struct SettingsView: View {
    @Bindable var app: AppModel
    @State private var confirmingDataDeletion = false

    var body: some View {
        NavigationStack {
            ZStack {
                EchoBackground()
                Form {
                    // MARK: Emergency stop
                    Section {
                        VStack(alignment: .leading, spacing: EchoSpacing.sm) {
                            Label(
                                app.settings.automationPaused ? "All automation is paused" : "Automation is running",
                                systemImage: app.settings.automationPaused ? "pause.octagon.fill" : "bolt.fill"
                            )
                            .font(EchoFont.headline)
                            .foregroundStyle(app.settings.automationPaused ? EchoColor.danger : EchoColor.cyan)

                            if let pausedAt = app.settings.automationPausedAt, app.settings.automationPaused {
                                Text("Paused \(pausedAt.formatted(.relative(presentation: .named)))")
                                    .echoReadout()
                            }

                            Button(app.settings.automationPaused ? "Resume automation" : "Emergency pause") {
                                app.setAutomationPaused(!app.settings.automationPaused)
                            }
                            .buttonStyle(.neon(app.settings.automationPaused ? .primary : .destructive))
                        }
                        .padding(.vertical, EchoSpacing.xs)
                    } footer: {
                        Text("Stops every automatic reply and scheduled send immediately. Drafts and notifications continue.")
                    }
                    .listRowBackground(EchoColor.surface.opacity(0.7))

                    // MARK: Automatic replies (Phase 5)
                    AutoReplySettingsSection(store: app.messagingStore)

                    // MARK: Location + calendar context
                    ContextSettingsSection(store: app.messagingStore)

                    // MARK: Instagram
                    InstagramSettingsSection(app: app)

                    // MARK: AI / backend
                    BackendSettingsSection(app: app)

                    // MARK: Notifications + quiet hours (Phase 6)
                    NotificationSettingsSection(app: app)

                    // MARK: Privacy
                    Section {
                        LabeledContent("Scheduling timezone", value: app.settings.defaultTimeZoneID)
                        Picker("Keep friends' messages", selection: $app.settings.messageRetentionDays) {
                            ForEach([7, 14, 30, 60, 90], id: \.self) { days in
                                Text("\(days) days").tag(days)
                            }
                        }
                        .tint(EchoColor.cyan)
                        Button("Delete all ECHO data on this device", role: .destructive) {
                            confirmingDataDeletion = true
                        }
                    } header: {
                        Text("Privacy")
                    } footer: {
                        Text("ECHO keeps only the recent messages it needs for context. Disconnecting Instagram (above) can also delete everything ECHO stored on your Mac.")
                    }
                    .listRowBackground(EchoColor.surface.opacity(0.7))

                    Section {
                        LabeledContent("Version", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "–")
                    }
                    .listRowBackground(EchoColor.surface.opacity(0.7))
                }
                .scrollContentBackground(.hidden)
            }
            .navigationTitle("Settings")
            .confirmationDialog(
                "Delete all ECHO data on this device?",
                isPresented: $confirmingDataDeletion,
                titleVisibility: .visible
            ) {
                Button("Delete everything", role: .destructive) {
                    app.deleteAllLocalData()
                }
            } message: {
                Text("Removes your chat history, learned profile and settings from this iPhone.")
            }
        }
    }
}
