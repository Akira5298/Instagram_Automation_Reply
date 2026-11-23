import Foundation
import Observation
import UIKit
import UserNotifications

/// Where a tapped notification should take you.
enum PushRoute: Hashable, Sendable {
    case conversation(UUID)
    case reviews
    case activity
}

/// Push permission, the APNs device token and taps on notifications.
/// Fed by `AppDelegate`; observed by the root view.
@MainActor
@Observable
final class PushManager {
    static let shared = PushManager()

    private(set) var authorization: UNAuthorizationStatus = .notDetermined
    /// Hex APNs token, once iOS has registered this device.
    private(set) var deviceToken: String?
    private(set) var registrationError: String?
    /// Set when you tap a notification; the root view navigates and clears it.
    var route: PushRoute?

    /// False unless the build enables push (needs the paid Apple Developer Program).
    /// While false, ECHO never asks iOS for a push token, so nothing can fail.
    static var isAvailable: Bool {
        (Bundle.main.object(forInfoDictionaryKey: "EchoPushEnabled") as? String)?.uppercased() == "YES"
    }

    /// Builds run from Xcode talk to APNs' sandbox; TestFlight/App Store builds to production.
    static var environment: String {
        #if DEBUG
        return "sandbox"
        #else
        return "production"
        #endif
    }

    func refreshStatus() async {
        guard Self.isAvailable else { return }
        authorization = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        if authorization == .authorized || authorization == .provisional {
            UIApplication.shared.registerForRemoteNotifications()   // tokens can change; re-register each launch
        }
    }

    /// Shows the iOS permission prompt (once). Returns whether notifications are allowed.
    @discardableResult
    func requestPermission() async -> Bool {
        guard Self.isAvailable else { return false }
        let granted = (try? await UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .sound, .badge])) ?? false
        await refreshStatus()
        return granted
    }

    func didRegister(token: Data) {
        deviceToken = token.map { String(format: "%02x", $0) }.joined()
        registrationError = nil
    }

    func didFailToRegister(_ error: Error) {
        registrationError = error.localizedDescription
    }

    func handleTap(friendID: String?, category: String?) {
        if category == "needs_attention" {
            route = .reviews
        } else if let friendID, let id = UUID(uuidString: friendID) {
            route = .conversation(id)
        } else {
            route = .activity
        }
    }
}
