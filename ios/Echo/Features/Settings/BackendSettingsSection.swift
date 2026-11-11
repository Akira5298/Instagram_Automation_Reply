import SwiftUI

/// Settings → "AI & backend": choose mock or your server, store the API token
/// in the Keychain, and test the connection.
struct BackendSettingsSection: View {
    @Bindable var app: AppModel

    @State private var tokenDraft = ""
    @State private var urlDraft = ""
    @State private var checks: [ConnectionCheck] = []
    @State private var isTesting = false
    @State private var tokenError: String?
    @State private var isSeeding = false

    private var useBackend: Binding<Bool> {
        Binding(
            get: { !app.settings.useMockServices },
            set: { newValue in
                app.settings.useMockServices = !newValue
                app.applyConnectionSettings()
                checks = []
            }
        )
    }

    var body: some View {
        Section {
            Toggle(isOn: useBackend) {
                Label("Use my backend (Claude)", systemImage: "server.rack")
            }
            .tint(EchoColor.neonPurple)

            statusRow

            LabeledContent("Backend URL") {
                TextField("http://localhost:8000", text: $urlDraft)
                    .multilineTextAlignment(.trailing)
                    .keyboardType(.URL)
                    .textContentType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .foregroundStyle(EchoColor.textSecondary)
                    .submitLabel(.done)
                    .onSubmit(commitURL)
            }

            if app.hasBackendToken {
                LabeledContent("API token") {
                    Label("Saved in Keychain", systemImage: "lock.fill")
                        .foregroundStyle(EchoColor.success)
                }
                Button("Remove token", role: .destructive) {
                    app.removeBackendToken()
                    checks = []
                }
            } else {
                SecureField("Paste ECHO_API_TOKEN", text: $tokenDraft)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button("Save token") {
                    do {
                        try app.saveBackendToken(tokenDraft)
                        tokenDraft = ""
                        tokenError = nil
                        Haptics.success()
                    } catch {
                        tokenError = error.localizedDescription
                    }
                }
                .disabled(tokenDraft.trimmingCharacters(in: .whitespaces).isEmpty)
                if let tokenError {
                    Text(tokenError).font(EchoFont.caption).foregroundStyle(EchoColor.danger)
                }
            }

            Button {
                commitURL()
                Task {
                    isTesting = true
                    checks = await app.testConnection()
                    isTesting = false
                }
            } label: {
                HStack {
                    Label("Test connection", systemImage: "antenna.radiowaves.left.and.right")
                    Spacer()
                    if isTesting { ProgressView() }
                }
            }
            .disabled(isTesting)

            ForEach(checks) { check in
                HStack(alignment: .firstTextBaseline) {
                    Image(systemName: check.ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
                        .foregroundStyle(check.ok ? EchoColor.success : EchoColor.danger)
                    Text(check.title)
                    Spacer()
                    Text(check.detail)
                        .font(EchoFont.caption)
                        .foregroundStyle(EchoColor.textSecondary)
                        .multilineTextAlignment(.trailing)
                }
                .accessibilityElement(children: .combine)
            }

            if app.connectionMode.isBackend {
                Button {
                    Task {
                        isSeeding = true
                        await app.loadSampleFriends()
                        isSeeding = false
                        Haptics.success()
                    }
                } label: {
                    HStack {
                        Label("Load sample friends (mock Instagram)", systemImage: "person.2.badge.plus")
                        Spacer()
                        if isSeeding { ProgressView() }
                    }
                }
                .disabled(isSeeding)
            }

            LabeledContent("Claude API key") {
                Text("Stored on your server").foregroundStyle(EchoColor.textTertiary)
            }
        } header: {
            Text("AI & backend")
        } footer: {
            Text("Sample friends live on your server's mock Instagram so you can test scheduling end to end (development only). The app never holds your Claude API key or Meta secrets — only a token for your own backend, kept in the iOS Keychain. In the Simulator use http://localhost:8000. On an iPhone, use your Mac's local IP (e.g. http://192.168.1.20:8000) or your deployed https URL.")
        }
        .listRowBackground(EchoColor.surface.opacity(0.7))
        .onAppear { urlDraft = app.settings.backendBaseURL }
    }

    @ViewBuilder
    private var statusRow: some View {
        switch app.connectionMode {
        case .mock:
            Label("Mock AI on this iPhone", systemImage: "testtube.2")
                .foregroundStyle(EchoColor.warning)
        case .backend(let url):
            Label("Connected to \(url.host() ?? url.absoluteString)", systemImage: "checkmark.seal.fill")
                .foregroundStyle(EchoColor.success)
        case .misconfigured(let reason):
            Label(reason, systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(EchoColor.warning)
        }
    }

    private func commitURL() {
        let trimmed = urlDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed != app.settings.backendBaseURL else { return }
        app.settings.backendBaseURL = trimmed
        app.applyConnectionSettings()
    }
}
