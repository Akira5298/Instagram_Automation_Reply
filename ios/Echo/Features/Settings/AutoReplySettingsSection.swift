import SwiftUI

/// Account-wide automatic-reply settings, stored on your server.
struct AutoReplySettingsSection: View {
    let store: MessagingStore
    @State private var settings: AutomationSettings?
    @State private var saved: AutomationSettings?

    private static let takeoverOptions = [0, 10, 30, 60, 120, 240]
    private static let limitOptions = [10, 25, 50, 100, 200, 500]

    var body: some View {
        Section {
            if let binding = Binding($settings) {
                Picker("After I reply myself, wait", selection: binding.takeoverMinutes) {
                    ForEach(Self.takeoverOptions, id: \.self) { minutes in
                        Text(minutes == 0 ? "Don't wait" : minutes < 60 ? "\(minutes) min" : "\(minutes / 60) h").tag(minutes)
                    }
                }
                Picker("Max auto replies per day", selection: binding.dailyReplyLimit) {
                    ForEach(Self.limitOptions, id: \.self) { Text("\($0)").tag($0) }
                }
                NavigationLink("Consent request message") {
                    TextEditorScreen(title: "Consent request", text: binding.consentRequestText,
                                     footer: "Sent when you tap “Ask by DM” on a friend. Keep the words YES and STOP — ECHO recognises them in the reply.")
                }
                NavigationLink("AI disclosure message") {
                    TextEditorScreen(title: "AI disclosure", text: binding.aiDisclosureText,
                                     footer: "Sent once before ECHO's first automatic message to someone, and again after 30 days.")
                }
            } else if store.isRemote {
                ProgressView()
            } else {
                Text("Connect your backend to change these.").foregroundStyle(EchoColor.textTertiary)
            }
        } header: {
            Text("Automatic replies")
        } footer: {
            Text("ECHO only replies to friends who agreed and have AI replies on, inside Instagram's 24-hour window. Each friend also has their own delay, review and style settings in Messages.")
        }
        .tint(EchoColor.cyan)
        .listRowBackground(EchoColor.surface.opacity(0.7))
        .task {
            let loaded = await store.loadAutomationSettings()
            settings = loaded
            saved = loaded
        }
        .onChange(of: settings) { _, newValue in
            guard let newValue, newValue != saved else { return }
            Task {
                try? await Task.sleep(for: .milliseconds(600))   // debounce typing
                guard newValue == settings else { return }
                if let result = await store.saveAutomationSettings(newValue) {
                    saved = result
                }
            }
        }
    }
}

private struct TextEditorScreen: View {
    let title: String
    @Binding var text: String
    let footer: String
    @State private var draft = ""

    var body: some View {
        ZStack {
            EchoBackground()
            Form {
                Section {
                    TextEditor(text: $draft)
                        .frame(minHeight: 140)
                } footer: {
                    Text(footer)
                }
                .listRowBackground(EchoColor.surface.opacity(0.7))
            }
            .scrollContentBackground(.hidden)
        }
        .navigationTitle(title)
        .onAppear { draft = text }
        .onDisappear {
            let trimmed = draft.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.count >= 10 { text = trimmed }
        }
    }
}
