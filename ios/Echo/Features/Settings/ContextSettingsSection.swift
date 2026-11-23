import EventKit
import SwiftUI
import UIKit

/// "Your day": optional city-level location and calendar so replies know when you're
/// busy. Everything stays off until you turn it on, and iOS asks permission first.
struct ContextSettingsSection: View {
    let store: MessagingStore
    @State private var context = ContextManager.shared
    @State private var working = false

    var body: some View {
        Section {
            Toggle("Share my city", isOn: Binding(
                get: { context.settings.shareLocation && context.locationAllowed },
                set: { on in Task { await setLocation(on) } }))
            if context.locationStatus == .denied || context.locationStatus == .restricted {
                deniedRow("Location is off for ECHO in iOS Settings.")
            }

            Toggle("Use my calendar", isOn: Binding(
                get: { context.settings.shareCalendar && context.calendarAllowed },
                set: { on in Task { await setCalendar(on) } }))
            if context.calendarStatus == .denied || context.calendarStatus == .restricted
                || context.calendarStatus == .writeOnly {
                deniedRow("Calendar access is off for ECHO in iOS Settings.")
            }
            if context.settings.shareCalendar {
                Toggle("Include event titles", isOn: Binding(
                    get: { context.settings.includeEventTitles },
                    set: { context.settings.includeEventTitles = $0; sync() }))
            }

            if context.settings.shareLocation || context.settings.shareCalendar {
                VStack(alignment: .leading, spacing: 4) {
                    Text("What ECHO knows right now").font(EchoFont.caption).foregroundStyle(EchoColor.textSecondary)
                    if let place = context.lastPlace {
                        Label([place.locality, place.country].compactMap { $0 }.joined(separator: ", "),
                              systemImage: "building.2")
                    }
                    if let count = context.lastEventCount {
                        Label(count == 0 ? "Nothing in the next 36 h" : "\(count) calendar block\(count == 1 ? "" : "s") in the next 36 h",
                              systemImage: "calendar")
                    }
                    if let last = context.lastUpload {
                        Text("Updated \(last.formatted(.relative(presentation: .named)))").echoReadout()
                    }
                }
                .font(EchoFont.footnote)
                Button("Stop sharing and delete", role: .destructive) {
                    Task { await context.stopSharing(store: store) }
                }
            }
        } header: {
            Text("Your day")
        } footer: {
            Text("Helps replies like “busy till 5, call later?”. Only your city (never GPS coordinates) and busy times for the next 36 hours go to your own server, refreshed about every 15 minutes while ECHO is open. ECHO never volunteers where you are or what you're doing: a draft that names your city or an event waits for your OK.")
        }
        .tint(EchoColor.cyan)
        .listRowBackground(EchoColor.surface.opacity(0.7))
        .disabled(working || !store.isRemote)
    }

    private func deniedRow(_ text: String) -> some View {
        Button {
            if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
        } label: {
            Label(text, systemImage: "gearshape").font(EchoFont.caption)
        }
    }

    private func setLocation(_ on: Bool) async {
        working = true
        defer { working = false }
        var granted = false
        if on { granted = await context.requestLocation() }
        context.settings.shareLocation = granted
        await context.sync(to: store, force: true)
    }

    private func setCalendar(_ on: Bool) async {
        working = true
        defer { working = false }
        var granted = false
        if on { granted = await context.requestCalendar() }
        context.settings.shareCalendar = granted
        await context.sync(to: store, force: true)
    }

    private func sync() {
        Task { await context.sync(to: store, force: true) }
    }
}
