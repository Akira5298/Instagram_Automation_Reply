import CoreLocation
import EventKit
import Foundation
import Observation

/// What you let ECHO know about your day. Off by default; stored on this phone only.
struct ContextSharingSettings: Codable, Equatable, Sendable {
    var shareLocation = false
    var shareCalendar = false
    /// Off: ECHO only sees busy/free blocks. On: event titles too (never sent to friends as-is).
    var includeEventTitles = false
}

/// Body of `PUT /v1/context`. City-level place and the next 36 hours of calendar.
struct UserContextUpload: Encodable, Sendable, Equatable {
    struct Place: Encodable, Sendable, Equatable {
        var locality: String?
        var region: String?
        var country: String?
    }

    struct Event: Encodable, Sendable, Equatable {
        var start: Date
        var end: Date
        var allDay: Bool
        var busy: Bool
        var title: String?
    }

    var location: Place?
    var events: [Event]?
    var clearLocation = false
    var clearCalendar = false
}

/// Location (city level, never GPS coordinates) and Apple Calendar, with permission.
/// The phone turns them into a small summary and uploads it to *your* server, which
/// only uses it while drafting: a draft that names your city or an event is held
/// for you in "Needs you" rather than sent.
@MainActor
@Observable
final class ContextManager: NSObject {
    static let shared = ContextManager()

    static let horizon: TimeInterval = 36 * 3600
    static let uploadInterval: TimeInterval = 15 * 60
    nonisolated static let maxEvents = 30

    var settings: ContextSharingSettings {
        didSet {
            try? settingsStore.save(settings)
            if settings != oldValue { lastUpload = nil }
        }
    }

    private(set) var locationStatus: CLAuthorizationStatus
    private(set) var calendarStatus: EKAuthorizationStatus
    private(set) var lastPlace: UserContextUpload.Place?
    private(set) var lastEventCount: Int?
    private(set) var lastUpload: Date?

    @ObservationIgnored private let settingsStore: LocalJSONStore<ContextSharingSettings>
    @ObservationIgnored private let locationManager: CLLocationManager
    @ObservationIgnored private let eventStore: EKEventStore
    @ObservationIgnored private var locationContinuation: CheckedContinuation<CLLocation?, Never>?
    @ObservationIgnored private var authContinuation: CheckedContinuation<Void, Never>?

    override init() {
        let store = LocalJSONStore<ContextSharingSettings>(filename: "context-sharing.json")
        let manager = CLLocationManager()
        self.settingsStore = store
        self.locationManager = manager
        self.eventStore = EKEventStore()
        self.settings = store.load() ?? ContextSharingSettings()
        self.locationStatus = manager.authorizationStatus
        self.calendarStatus = EKEventStore.authorizationStatus(for: .event)
        self.lastPlace = nil
        self.lastEventCount = nil
        self.lastUpload = nil
        super.init()
        locationManager.delegate = self
        locationManager.desiredAccuracy = kCLLocationAccuracyKilometer
    }

    var locationAllowed: Bool { locationStatus == .authorizedWhenInUse || locationStatus == .authorizedAlways }
    var calendarAllowed: Bool { calendarStatus == .fullAccess }

    // MARK: Permissions

    /// Asks iOS for "While Using the App" location. Returns whether it was granted.
    func requestLocation() async -> Bool {
        if locationStatus == .notDetermined {
            await withCheckedContinuation { continuation in
                authContinuation = continuation
                locationManager.requestWhenInUseAuthorization()
            }
        }
        return locationAllowed
    }

    func requestCalendar() async -> Bool {
        do {
            _ = try await eventStore.requestFullAccessToEvents()
        } catch {
            // Denied or restricted; the status below says which.
        }
        calendarStatus = EKEventStore.authorizationStatus(for: .event)
        return calendarAllowed
    }

    // MARK: Upload

    /// Builds the summary and sends it. Throttled to every 15 minutes unless `force`.
    func sync(to store: MessagingStore, force: Bool = false) async {
        guard store.isRemote else { return }
        if !force, let lastUpload, Date.now.timeIntervalSince(lastUpload) < Self.uploadInterval { return }
        let upload = await buildUpload()
        await store.uploadContext(upload)
        lastUpload = .now
    }

    /// Stop sharing everything and delete what the server has.
    func stopSharing(store: MessagingStore) async {
        settings.shareLocation = false
        settings.shareCalendar = false
        lastPlace = nil
        lastEventCount = nil
        await store.clearContext()
    }

    func buildUpload(now: Date = .now) async -> UserContextUpload {
        var upload = UserContextUpload()
        if settings.shareLocation, locationAllowed, let place = await currentPlace() {
            upload.location = place
            lastPlace = place
        } else if !settings.shareLocation {
            upload.clearLocation = true
            lastPlace = nil
        }
        if settings.shareCalendar, calendarAllowed {
            let events = upcomingEvents(now: now)
            upload.events = events
            lastEventCount = events.count
        } else if !settings.shareCalendar {
            upload.clearCalendar = true
            lastEventCount = nil
        }
        return upload
    }

    /// Busy blocks (and optionally titles) for the next 36 hours. Cancelled events are
    /// dropped, "free" ones are marked as such; locations, notes and attendees never leave the phone.
    func upcomingEvents(now: Date = .now) -> [UserContextUpload.Event] {
        let predicate = eventStore.predicateForEvents(withStart: now, end: now.addingTimeInterval(Self.horizon),
                                                      calendars: nil)
        return Self.summarise(eventStore.events(matching: predicate).map {
            RawEvent(start: $0.startDate, end: $0.endDate, allDay: $0.isAllDay,
                     free: $0.availability == .free, cancelled: $0.status == .canceled, title: $0.title)
        }, includeTitles: settings.includeEventTitles)
    }

    struct RawEvent: Sendable {
        var start: Date
        var end: Date
        var allDay: Bool
        var free: Bool
        var cancelled: Bool
        var title: String?
    }

    /// Pure, so it's testable without EventKit.
    nonisolated static func summarise(_ raw: [RawEvent], includeTitles: Bool) -> [UserContextUpload.Event] {
        raw.filter { !$0.cancelled }
            .sorted { $0.start < $1.start }
            .prefix(maxEvents)
            .map { event in
                let title = includeTitles ? event.title.map { String($0.prefix(120)) } : nil
                return UserContextUpload.Event(start: event.start, end: event.end, allDay: event.allDay,
                                               busy: !event.free, title: title?.isEmpty == true ? nil : title)
            }
    }

    private func currentPlace() async -> UserContextUpload.Place? {
        let location: CLLocation? = await withCheckedContinuation { continuation in
            locationContinuation?.resume(returning: nil)
            locationContinuation = continuation
            locationManager.requestLocation()
        }
        guard let location,
              let placemark = try? await CLGeocoder().reverseGeocodeLocation(location).first else { return nil }
        return UserContextUpload.Place(locality: placemark.locality, region: placemark.administrativeArea,
                                       country: placemark.country)
    }
}

extension ContextManager: CLLocationManagerDelegate {
    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        Task { @MainActor in
            self.locationStatus = status
            if status != .notDetermined {
                self.authContinuation?.resume()
                self.authContinuation = nil
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        let last = locations.last
        Task { @MainActor in
            self.locationContinuation?.resume(returning: last)
            self.locationContinuation = nil
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor in
            self.locationContinuation?.resume(returning: nil)
            self.locationContinuation = nil
        }
    }
}
