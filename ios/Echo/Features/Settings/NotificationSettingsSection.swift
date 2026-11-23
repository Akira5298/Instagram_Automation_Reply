import SwiftUI
import UIKit
import UserNotifications

/// Push permission, what to be notified about, and quiet hours. Preferences
/// are stored on your server (it decides what to push) and mirrored locally.
struct NotificationSettingsSection: View {
    @Bindable var app: AppModel
    @State private var push = PushManager.shared
    @State private var testResult: String?
    @State private var testing = false
    @State private var loadedFromServer = false
    /// Which connection the preferences were loaded for (load once per backend).
    @State private var loadedForRemote: Bool?
    /// What the server has, so loading doesn't trigger an identical save.
    @State private var syncedPrefs: NotificationPreferences?

    private var prefs: Binding<NotificationPreferences> { $app.settings.notifications }

    var body: some View {
        Section {
            pushRow
            Toggle("ECHO replied for me", isOn: prefs.messageSent)
            Toggle("A reply needs me", isOn: prefs.needsAttention)
            Toggle("Scheduled message blocked", isOn: prefs.jobBlocked)
            Toggle("Delivery failed", isOn: prefs.deliveryFailed)
            Toggle("Automation status changes", isOn: prefs.automationChanged)
            Toggle("Instagram connection errors", isOn: prefs.connectionError)
            Toggle("Upcoming schedule reminders", isOn: prefs.scheduleReminder)
        } header: {
            Text("Notifications")
        } footer: {
            Text(PushManager.isAvailable
                 ? "Your Mac sends these through Apple Push Notification service, so they arrive even when ECHO is closed. Everything also appears in the activity feed."
                 : "These choices decide what lands in your activity feed (and in push, once it's turned on).")
        }
        .tint(EchoColor.neonPurple)
        .listRowBackground(EchoColor.surface.opacity(0.7))

        Section {
            Toggle("Quiet hours", isOn: prefs.quietHoursEnabled)
            if app.settings.notifications.quietHoursEnabled {
                DatePicker("From", selection: time(prefs.quietStartMinutes), displayedComponents: .hourAndMinute)
                DatePicker("Until", selection: time(prefs.quietEndMinutes), displayedComponents: .hourAndMinute)
                Toggle("Don't auto-reply during quiet hours", isOn: prefs.holdRepliesInQuietHours)
            }
        } header: {
            Text("Quiet hours")
        } footer: {
            Text("No push notifications during quiet hours (times in \(app.settings.defaultTimeZoneID)). Turn on “Don't auto-reply” if instant replies at 3 AM would look odd — those messages wait for you instead.")
        }
        .tint(EchoColor.neonPurple)
        .listRowBackground(EchoColor.surface.opacity(0.7))
        .task(id: app.messagingStore.isRemote) {
            await push.refreshStatus()
            let remote = app.messagingStore.isRemote
            guard loadedForRemote != remote else { return }    // already loaded for this backend
            if let server = await app.messagingStore.loadNotificationPreferences(),
               server != app.settings.notifications {
                // Only write real changes: AppSettings is observed app-wide and saved to disk.
                syncedPrefs = server
                app.settings.notifications = server
            } else {
                syncedPrefs = app.settings.notifications
            }
            loadedForRemote = remote
            loadedFromServer = true
        }
        // Debounced save; `.task(id:)` cancels the previous wait on each change.
        .task(id: app.settings.notifications) {
            let pending = app.settings.notifications
            guard loadedFromServer, app.messagingStore.isRemote, pending != syncedPrefs else { return }
            do { try await Task.sleep(for: .milliseconds(500)) } catch { return }
            await app.messagingStore.saveNotificationPreferences(pending)
            syncedPrefs = pending
        }
    }

    @ViewBuilder
    private var pushRow: some View {
        if !PushManager.isAvailable {
            Label("Push notifications are off in this build", systemImage: "bell.slash")
                .foregroundStyle(EchoColor.textSecondary)
            Text("They need the paid Apple Developer Program. Until then, everything still shows up in the app (live) and in the activity feed. To turn push on later, see docs/TESTFLIGHT.md.")
                .font(EchoFont.caption)
                .foregroundStyle(EchoColor.textTertiary)
        } else {
            pushControls
        }
    }

    @ViewBuilder
    private var pushControls: some View {
        switch push.authorization {
        case .authorized, .provisional, .ephemeral:
            HStack {
                Label("Push is on", systemImage: "bell.badge.fill").foregroundStyle(EchoColor.success)
                Spacer()
                Button(testing ? "Sending…" : "Send test") { sendTest() }
                    .disabled(testing || !app.messagingStore.isRemote)
            }
            if let testResult {
                Text(testResult).font(EchoFont.caption).foregroundStyle(EchoColor.textSecondary)
            }
            if let error = push.registrationError {
                Text("iOS couldn't register for push: \(error)").font(EchoFont.caption).foregroundStyle(EchoColor.warning)
            }
        case .denied:
            Button {
                if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
            } label: {
                Label("Notifications are off — open iOS Settings", systemImage: "bell.slash")
            }
            .foregroundStyle(EchoColor.warning)
        default:
            Button {
                Task { await push.requestPermission() }
            } label: {
                Label("Turn on push notifications", systemImage: "bell")
            }
            .foregroundStyle(EchoColor.cyan)
        }
    }

    private func sendTest() {
        testing = true
        Task {
            testResult = await app.messagingStore.sendTestPush()
            testing = false
        }
    }

    /// Minutes-after-midnight ⇄ Date for DatePicker (only hour and minute matter).
    private func time(_ minutes: Binding<Int>) -> Binding<Date> {
        Binding(
            get: {
                Calendar.current.date(bySettingHour: minutes.wrappedValue / 60, minute: minutes.wrappedValue % 60,
                                      second: 0, of: .now) ?? .now
            },
            set: { date in
                let parts = Calendar.current.dateComponents([.hour, .minute], from: date)
                minutes.wrappedValue = (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
            }
        )
    }
}
