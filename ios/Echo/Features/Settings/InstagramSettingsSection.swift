import AuthenticationServices
import SwiftUI

/// Settings → Instagram: connect your Creator/Business account with Meta's
/// official Instagram Login. The login runs in a secure system browser sheet;
/// tokens stay on your server, never on the phone.
struct InstagramSettingsSection: View {
    @Bindable var app: AppModel

    @Environment(\.webAuthenticationSession) private var webAuthenticationSession
    @State private var isWorking = false
    @State private var message: String?
    @State private var confirmingDisconnect = false

    private var store: MessagingStore { app.messagingStore }
    private var status: InstagramStatus? { store.instagramStatus }

    var body: some View {
        Section {
            if !app.connectionMode.isBackend {
                Label("Connect your backend first (AI & backend below).", systemImage: "server.rack")
                    .foregroundStyle(EchoColor.textSecondary)
            } else if let status, status.connected, let username = status.username {
                connectedRows(status, username: username)
            } else {
                disconnectedRows
            }

            if let message {
                Text(message)
                    .font(EchoFont.caption)
                    .foregroundStyle(EchoColor.textSecondary)
            }
        } header: {
            Text("Instagram")
        } footer: {
            Text("Uses Meta's official Instagram API with Unified Login: sign in with Instagram or Facebook and pick your Creator or Business account. No Facebook Page needed. ECHO can only message people who messaged you first, within Instagram's 24-hour window.")
        }
        .listRowBackground(EchoColor.surface.opacity(0.7))
        .task(id: app.connectionMode) {
            if app.connectionMode.isBackend { await store.refreshInstagramStatus() }
        }
        .confirmationDialog("Disconnect Instagram?", isPresented: $confirmingDisconnect, titleVisibility: .visible) {
            Button("Disconnect, keep message history") { Task { await disconnect(deleteMessages: false) } }
            Button("Disconnect and delete Instagram data", role: .destructive) {
                Task { await disconnect(deleteMessages: true) }
            }
        } message: {
            Text("ECHO stops receiving and sending Instagram messages immediately. Scheduled messages will be blocked.")
        }
    }

    @ViewBuilder
    private func connectedRows(_ status: InstagramStatus, username: String) -> some View {
        LabeledContent("Account") {
            Label("@\(username)", systemImage: "checkmark.seal.fill")
                .foregroundStyle(EchoColor.success)
        }
        if let type = status.accountTypeTitle {
            LabeledContent("Type", value: type)
        }
        LabeledContent("Webhooks") {
            Text(status.webhooksSubscribed == true ? "Subscribed" : "Not subscribed")
                .foregroundStyle(status.webhooksSubscribed == true ? EchoColor.success : EchoColor.warning)
        }
        LabeledContent("Last webhook from Meta") {
            if let last = status.lastWebhookAt {
                Text(last, format: .relative(presentation: .named))
            } else {
                Text("None yet").foregroundStyle(EchoColor.warning)
            }
        }
        if status.lastWebhookAt == nil {
            Text("Meta hasn't delivered any webhooks yet, so new DMs are picked up by ECHO's 30-second check instead of instantly. See docs/META_SETUP.md §5 and §7 (the app must be Live).")
                .font(EchoFont.caption)
                .foregroundStyle(EchoColor.textTertiary)
        }
        if let expires = status.tokenExpiresAt {
            LabeledContent(status.loginMode == "instagram" ? "Login renews by" : "Login expires") {
                Text(expires, format: .dateTime.day().month(.abbreviated))
            }
        }
        if let error = status.lastError {
            Label(error, systemImage: "exclamationmark.triangle.fill")
                .font(EchoFont.caption)
                .foregroundStyle(EchoColor.warning)
        }
        Button {
            Task {
                isWorking = true
                if let count = await store.syncInstagram() {
                    message = count == 0 ? "Up to date." : "Synced \(count) new messages."
                }
                await store.refreshInstagramStatus()
                isWorking = false
            }
        } label: {
            HStack {
                Label("Sync 5 latest chats now", systemImage: "arrow.triangle.2.circlepath")
                Spacer()
                if isWorking { ProgressView() }
            }
        }
        .disabled(isWorking)
        Button("Disconnect Instagram", role: .destructive) { confirmingDisconnect = true }
    }

    @ViewBuilder
    private var disconnectedRows: some View {
        if let status, !status.configured {
            let missing = (status.missingSettings ?? []).joined(separator: ", ")
            Label("The server isn't ready for Instagram yet: it needs \(missing.isEmpty ? "the META_* settings" : missing) and a running Cloudflare Tunnel (scripts/start.sh). See docs/META_SETUP.md.",
                  systemImage: "wrench.and.screwdriver")
                .font(EchoFont.caption)
                .foregroundStyle(EchoColor.warning)
        }
        if let status, status.needsReconnect == true, status.status != "expired" {
            Label("ECHO now uses Meta's Unified Login. Connect again to keep messaging.",
                  systemImage: "arrow.triangle.2.circlepath")
                .font(EchoFont.caption)
                .foregroundStyle(EchoColor.warning)
        }
        if let status, status.status == "expired" {
            Label("Your Instagram login expired. Reconnect to resume.", systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(EchoColor.warning)
        }
        Button {
            Task { await connect() }
        } label: {
            HStack {
                Label("Connect Instagram", systemImage: "link")
                Spacer()
                if isWorking { ProgressView() }
            }
        }
        .disabled(isWorking || status?.configured == false)
    }

    private func connect() async {
        isWorking = true
        defer { isWorking = false }
        guard let url = await store.instagramAuthorizeURL() else { return }
        do {
            let callback = try await webAuthenticationSession.authenticate(
                using: url,
                callbackURLScheme: "echo",
                preferredBrowserSession: .ephemeral
            )
            let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let value = { (name: String) in items.first { $0.name == name }?.value }
            switch value("status") {
            case "ok":
                message = "Connected as @\(value("username") ?? "")."
                    + (value("webhooks") == "1" ? "" : " Webhook subscription failed — see docs/META_SETUP.md.")
                Haptics.success()
                await store.refresh()
            case "cancelled":
                message = "Login cancelled."
            default:
                message = value("message") ?? "Instagram login failed."
            }
        } catch let error as ASWebAuthenticationSessionError where error.code == .canceledLogin {
            message = "Login cancelled."
        } catch {
            message = error.localizedDescription
        }
        await store.refreshInstagramStatus()
    }

    private func disconnect(deleteMessages: Bool) async {
        isWorking = true
        await store.disconnectInstagram(deleteMessages: deleteMessages)
        message = "Instagram disconnected."
        isWorking = false
    }
}
