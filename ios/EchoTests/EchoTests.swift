import XCTest
@testable import Echo

final class MockStyleAnalyzerTests: XCTestCase {
    private let analyzer = MockStyleAnalyzer()

    func testDetectsLanguages() {
        XCTAssertEqual(MockStyleAnalyzer.detectLanguage("今日は暇だよ"), "ja")
        XCTAssertEqual(MockStyleAnalyzer.detectLanguage("我今天很忙"), "zh-Hans")
        XCTAssertEqual(MockStyleAnalyzer.detectLanguage("ciao, come stai?"), "it")
        XCTAssertEqual(MockStyleAnalyzer.detectLanguage("hey what's up"), "en")
    }

    func testStatedInterestIsAFactNotAGuess() {
        let analysis = analyzer.analyze("ngl I love bouldering lol")
        let interest = analysis.updates.first { $0.category == .interests }
        XCTAssertEqual(interest?.summary, "bouldering")
        XCTAssertEqual(interest?.kind, .statedFact)
        XCTAssertTrue(analysis.updates.contains { $0.key == "slang.ngl" && $0.kind == .observation })
    }

    func testMultilingualInterests() {
        XCTAssertEqual(MockStyleAnalyzer.statedInterests(in: "mi piace la pizza"), ["la pizza"])
        XCTAssertEqual(MockStyleAnalyzer.statedInterests(in: "我喜欢篮球"), ["篮球"])
        XCTAssertFalse(MockStyleAnalyzer.statedInterests(in: "ラーメンが好き").isEmpty)
    }

    func testEmojiAndShortMessages() {
        let keys = analyzer.analyze("lol 😂😂").updates.map(\.key)
        XCTAssertTrue(keys.contains("emoji.uses"))
        XCTAssertTrue(keys.contains("length.short"))
    }
}

@MainActor
final class ProfileMemoryTests: XCTestCase {
    private func makeStore(file: LocalJSONStore<CommunicationProfile>? = nil) -> ProfileStore {
        let file = file ?? LocalJSONStore(filename: "test-profile-\(UUID()).json", directory: .temporaryDirectory)
        let repository = LocalProfileRepository(store: file)
        return ProfileStore(repository: repository, initial: repository.profile)
    }

    private func observation(_ key: String, confidence: Double = 0.4) -> MemoryUpdate {
        MemoryUpdate(category: .slang, key: key, summary: key, kind: .observation, confidence: confidence)
    }

    func testRepeatedEvidenceRaisesConfidenceButNeverToCertainty() async {
        let store = makeStore()
        for _ in 0..<30 { await store.didLearn([observation("slang.ngl", confidence: 0.9)]) }
        let trait = store.profile.traits.first
        XCTAssertEqual(store.profile.traits.count, 1)
        XCTAssertEqual(trait?.evidenceCount, 30)
        XCTAssertLessThanOrEqual(trait?.confidence ?? 1, 0.95)
        XCTAssertEqual(trait?.source, .inferred)
        XCTAssertNotNil(store.lastLearnedAt)
    }

    func testNewObservationIsCappedAtSixtyPercent() async {
        let store = makeStore()
        await store.didLearn([observation("slang.ngl", confidence: 0.99)])
        XCTAssertEqual(store.profile.traits.first?.confidence, 0.6)
    }

    func testUserEditsAreNeverOverwrittenByInference() async {
        let store = makeStore()
        await store.didLearn([observation("slang.ngl")])
        var trait = try! XCTUnwrap(store.profile.traits.first)
        trait.value = "Only uses ngl with close friends"
        await store.save(trait)
        await store.didLearn([observation("slang.ngl")])
        XCTAssertEqual(store.profile.traits.first?.value, "Only uses ngl with close friends")
        XCTAssertEqual(store.profile.traits.first?.source, .userEdited)
    }

    func testDeletedTraitsAreNotRelearned() async {
        let store = makeStore()
        await store.didLearn([observation("slang.ngl")])
        await store.delete(try! XCTUnwrap(store.profile.traits.first))
        await store.didLearn([observation("slang.ngl")])
        XCTAssertTrue(store.profile.traits.isEmpty)
    }

    func testRemovalForgetsAnInterest() async {
        let store = makeStore()
        await store.didLearn([MemoryUpdate(category: .interests, key: "interests.anime", summary: "Loves anime",
                                           kind: .statedFact, confidence: 1)])
        await store.didLearn([MemoryUpdate(op: .remove, category: .interests, key: "interests.anime", summary: "",
                                           kind: .statedFact, confidence: 1)])
        XCTAssertTrue(store.profile.traits.isEmpty)
        XCTAssertFalse(store.profile.rejectedKeys.contains("interests.anime"), "removal isn't a deletion")
    }

    func testWritingExamples() async {
        let store = makeStore()
        await store.addExample(text: "omw!! 🏃", language: "en")
        await store.addExample(text: "omw!! 🏃", language: "en") // duplicate ignored
        XCTAssertEqual(store.profile.examples.count, 1)
        await store.deleteExample(store.profile.examples[0])
        XCTAssertTrue(store.profile.examples.isEmpty)
    }

    func testPersistsAcrossInstances() async {
        let file = LocalJSONStore<CommunicationProfile>(filename: "test-persist-\(UUID()).json", directory: .temporaryDirectory)
        await makeStore(file: file).didLearn([observation("slang.tbh")])
        XCTAssertEqual(makeStore(file: file).profile.traits.first?.key, "slang.tbh")
        file.delete()
    }
}

/// Chat service double that fails on demand.
@MainActor
final class FlakyChatService: ChatService {
    var failuresRemaining = 0
    var receivedIDs: [UUID] = []
    var isRemote: Bool { true }

    func history() async throws -> [ChatMessage]? { [] }
    func clearHistory() async throws {}

    func reply(to message: ChatMessage, history: [ChatMessage]) async throws -> ChatMessage {
        receivedIDs.append(message.id)
        if failuresRemaining > 0 {
            failuresRemaining -= 1
            throw APIError.transport("offline")
        }
        return ChatMessage(role: .assistant, text: "hi!", memoryUpdates: [
            MemoryUpdate(category: .interests, key: "interests.jazz", summary: "Loves jazz", kind: .statedFact, confidence: 1),
        ])
    }
}

@MainActor
final class ChatViewModelTests: XCTestCase {
    private func makeViewModel(service: ChatService) -> (ChatViewModel, ProfileStore) {
        let repository = LocalProfileRepository(
            store: LocalJSONStore(filename: "test-p-\(UUID()).json", directory: .temporaryDirectory)
        )
        let profile = ProfileStore(repository: repository)
        let viewModel = ChatViewModel(
            service: service,
            profileStore: profile,
            store: LocalJSONStore(filename: "test-c-\(UUID()).json", directory: .temporaryDirectory)
        )
        return (viewModel, profile)
    }

    func testSendAppendsReplyAndLearns() async {
        let (viewModel, profile) = makeViewModel(service: MockChatService(simulateLatency: false))
        XCTAssertEqual(viewModel.messages.count, 1, "starts with the greeting")

        viewModel.draft = "I love bouldering"
        XCTAssertTrue(viewModel.canSend)
        await viewModel.send()

        XCTAssertEqual(viewModel.messages.count, 3)
        XCTAssertEqual(viewModel.messages[1].role, .user)
        XCTAssertNil(viewModel.messages[1].deliveryState)
        XCTAssertEqual(viewModel.messages[2].role, .assistant)
        XCTAssertTrue(viewModel.draft.isEmpty)
        XCTAssertFalse(viewModel.isAwaitingReply)
        XCTAssertTrue(profile.profile.traits.contains { $0.category == .interests && $0.value == "bouldering" })
    }

    func testBlankMessageIsIgnored() async {
        let (viewModel, _) = makeViewModel(service: MockChatService(simulateLatency: false))
        viewModel.draft = "   "
        XCTAssertFalse(viewModel.canSend)
        await viewModel.send()
        XCTAssertEqual(viewModel.messages.count, 1)
    }

    func testFailedMessageCanBeRetriedWithTheSameID() async {
        let service = FlakyChatService()
        service.failuresRemaining = 1
        let (viewModel, profile) = makeViewModel(service: service)

        viewModel.draft = "I love jazz"
        await viewModel.send()
        let failed = try! XCTUnwrap(viewModel.messages.last)
        XCTAssertEqual(failed.deliveryState, .failed)
        XCTAssertNotNil(viewModel.errorMessage)

        await viewModel.retry(failed)
        XCTAssertEqual(viewModel.messages.count, 3)
        XCTAssertNil(viewModel.messages[1].deliveryState)
        XCTAssertEqual(service.receivedIDs, [failed.id, failed.id], "retry reuses the idempotency key")
        XCTAssertEqual(profile.profile.traits.first?.key, "interests.jazz")
    }
}

@MainActor
final class SchedulerViewModelTests: XCTestCase {
    func testNewJobIsScheduledButEligibilityIsDeferred() async {
        let store = MessagingStore.mock()
        let viewModel = SchedulerViewModel(store: store)
        let friend = try! XCTUnwrap(store.friends.first)
        let before = store.jobs.count

        let ok = await viewModel.createJob(
            friend: friend,
            date: .now.addingTimeInterval(3600),
            recurrence: .weekly,
            instruction: "  Ask about the weekend  ",
            automationEnabled: true
        )

        XCTAssertTrue(ok)
        let job = try! XCTUnwrap(store.jobs.last)
        XCTAssertEqual(store.jobs.count, before + 1)
        XCTAssertEqual(job.status, .scheduled)
        XCTAssertEqual(job.eligibility, .checkedAtDispatch)
        XCTAssertEqual(job.timeZoneID, "Asia/Kuala_Lumpur")
        XCTAssertEqual(job.instruction, "Ask about the weekend")
        XCTAssertEqual(job.recurrence.rrule, "FREQ=WEEKLY")
    }

    func testPastTimeIsRejected() async {
        let store = MessagingStore.mock()
        let ok = await SchedulerViewModel(store: store).createJob(
            friend: store.friends[0], date: .now.addingTimeInterval(-60), recurrence: .once,
            instruction: "hi", automationEnabled: true
        )
        XCTAssertFalse(ok)
        XCTAssertNotNil(store.errorMessage)
    }

    func testPauseAndResume() async {
        let store = MessagingStore.mock()
        let viewModel = SchedulerViewModel(store: store)
        let job = try! XCTUnwrap(store.jobs.first { $0.status == .scheduled })
        await viewModel.togglePause(job)
        let paused = try! XCTUnwrap(store.jobs.first { $0.id == job.id })
        XCTAssertEqual(paused.status, .paused)
        await viewModel.togglePause(paused)
        XCTAssertEqual(store.jobs.first { $0.id == job.id }?.status, .scheduled)
    }

    func testOfflineRunNowAppliesTheSameRules() async {
        let store = MessagingStore.mock()
        // Giulia: consent only "requested" and last message 30h ago → blocked.
        let giulia = try! XCTUnwrap(store.friends.first { $0.displayName.hasPrefix("Giulia") })
        await SchedulerViewModel(store: store).createJob(
            friend: giulia, date: .now.addingTimeInterval(3600), recurrence: .once,
            instruction: "ask about study group", automationEnabled: true
        )
        let job = try! XCTUnwrap(store.jobs.last)
        await store.runNow(job)
        let updated = try! XCTUnwrap(store.jobs.first { $0.id == job.id })
        XCTAssertEqual(updated.lastRunStatus, .blocked)
        XCTAssertEqual(updated.eligibility, .noConsent)
        XCTAssertEqual(store.activity.first?.category, "job_blocked")
    }

    func testWeekIsSevenDaysInKualaLumpurTime() {
        let viewModel = SchedulerViewModel(store: .mock())
        let days = viewModel.weekDays()
        XCTAssertEqual(days.count, 7)
        XCTAssertEqual(viewModel.calendar.component(.hour, from: days[0]), 0)
    }
}

// MARK: - Networking

final class StubURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) -> (Int, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = Self.handler, let url = request.url else { return }
        let (status, data) = handler(request)
        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}

    static func session() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        return URLSession(configuration: configuration)
    }
}

@MainActor
final class BackendContractTests: XCTestCase {
    override func tearDown() {
        StubURLProtocol.handler = nil
        super.tearDown()
    }

    func testServerDatesWithMicrosecondsAndOffsets() {
        let a = ServerDate.parse("2026-11-11T11:05:57.313244+08:00")
        let b = ServerDate.parse("2026-11-11T03:05:57.313Z")
        let c = ServerDate.parse("2026-11-11T03:05:57Z")
        XCTAssertNotNil(a)
        XCTAssertEqual(a!.timeIntervalSince1970, b!.timeIntervalSince1970, accuracy: 0.001)
        XCTAssertEqual(c!.timeIntervalSince1970, b!.timeIntervalSince1970, accuracy: 1)
    }

    /// Exact JSON shape returned by the FastAPI backend (captured from a live run).
    func testChatTurnDecodesBackendResponse() async throws {
        let json = """
        {"user_message":{"id":"7b0d3a7e-1c1e-4a7a-9a55-1f6f7b0c2d11","role":"user","text":"ngl I love bouldering",
          "language":"en","created_at":"2026-11-11T11:05:57.313244+08:00","memory_updates":[]},
         "message":{"id":"12fb00a0-f101-44be-85de-48d396f23ef0","role":"assistant",
          "text":"Nice — what got you into it?","language":"en","created_at":"2026-11-11T11:05:57.322573+08:00",
          "memory_updates":[{"id":"4c99b0e1-2767-43ca-a2a7-4b824e5d4bc2","op":"set","category":"sentence_style",
            "key":"sentence_style.short_messages","summary":"Tends to write short messages","kind":"observation",
            "confidence":0.35},
           {"id":"84789eae-adc9-499b-95fd-d5ce7ffce263","op":"set","category":"interests","key":"interests.bouldering",
            "summary":"Loves bouldering","kind":"stated_fact","confidence":1.0}]},
         "replayed":false}
        """
        var captured: URLRequest?
        var capturedBody: [String: Any]?
        StubURLProtocol.handler = { request in
            captured = request
            if let stream = request.httpBodyStream {
                stream.open()
                var data = Data()
                var buffer = [UInt8](repeating: 0, count: 4096)
                while stream.hasBytesAvailable {
                    let count = stream.read(&buffer, maxLength: buffer.count)
                    if count <= 0 { break }
                    data.append(buffer, count: count)
                }
                stream.close()
                capturedBody = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            }
            return (200, Data(json.utf8))
        }
        let api = APIClient(baseURL: URL(string: "http://localhost:8000")!, token: "secret", session: StubURLProtocol.session())
        let service = BackendChatService(api: api)
        let outgoing = ChatMessage(id: UUID(uuidString: "7B0D3A7E-1C1E-4A7A-9A55-1F6F7B0C2D11")!, role: .user, text: "ngl I love bouldering")

        let reply = try await service.reply(to: outgoing, history: [])

        XCTAssertEqual(captured?.url?.absoluteString, "http://localhost:8000/v1/chat/messages")
        XCTAssertEqual(captured?.value(forHTTPHeaderField: "Authorization"), "Bearer secret")
        XCTAssertEqual((capturedBody?["client_msg_id"] as? String)?.lowercased(), "7b0d3a7e-1c1e-4a7a-9a55-1f6f7b0c2d11")
        XCTAssertEqual(reply.role, .assistant)
        XCTAssertEqual(reply.memoryUpdates.count, 2)
        XCTAssertEqual(reply.memoryUpdates[0].category, .sentenceStyle)
        XCTAssertEqual(reply.memoryUpdates[1].kind, .statedFact)
    }

    func testProfileDecodesBackendResponse() async throws {
        let json = """
        {"traits":[{"id":"2f1d1f9e-5a5d-4f43-9d3f-6b8f0b0e0a11","category":"friend_preferences",
          "key":"friend_preferences.close_friends_casual","value":"Very casual with close friends","source":"user_stated",
          "confidence":1.0,"evidence_count":2,"updated_at":"2026-11-11T03:05:57+00:00"}],
         "examples":[{"id":"9c1f1f9e-5a5d-4f43-9d3f-6b8f0b0e0a22","text":"omw!!","language":"en",
          "created_at":"2026-11-11T03:05:57Z"}],
         "rejected_keys":["slang.ngl"]}
        """
        StubURLProtocol.handler = { _ in (200, Data(json.utf8)) }
        let api = APIClient(baseURL: URL(string: "http://localhost:8000")!, token: "t", session: StubURLProtocol.session())
        let repository = RemoteProfileRepository(
            api: api,
            cache: LocalJSONStore(filename: "test-remote-\(UUID()).json", directory: .temporaryDirectory)
        )
        let profile = try await repository.load()
        XCTAssertEqual(profile.traits.first?.category, .friendPreferences)
        XCTAssertEqual(profile.traits.first?.source, .userStated)
        XCTAssertEqual(profile.traits.first?.evidenceCount, 2)
        XCTAssertEqual(profile.examples.first?.text, "omw!!")
        XCTAssertEqual(profile.rejectedKeys, ["slang.ngl"])
        XCTAssertEqual(repository.cachedProfile, profile, "last good copy is cached for offline use")
    }

    func testErrorsAreReadable() async {
        StubURLProtocol.handler = { _ in (503, Data(#"{"detail":"ECHO is busy right now. Try again in a minute."}"#.utf8)) }
        let api = APIClient(baseURL: URL(string: "http://localhost:8000")!, token: "t", session: StubURLProtocol.session())
        do {
            _ = try await BackendChatService(api: api).reply(to: ChatMessage(role: .user, text: "hi"), history: [])
            XCTFail("expected an error")
        } catch let error as APIError {
            XCTAssertEqual(error.errorDescription, "ECHO is busy right now. Try again in a minute.")
            XCTAssertTrue(error.isTransient)
        } catch {
            XCTFail("unexpected \(error)")
        }

        StubURLProtocol.handler = { _ in (401, Data(#"{"detail":"invalid token"}"#.utf8)) }
        do {
            _ = try await BackendChatService(api: api).history()
            XCTFail("expected an error")
        } catch {
            XCTAssertEqual(error as? APIError, .unauthorized)
        }
    }

    /// Exact JSON from the Phase 3 backend (captured from a live run).
    func testJobsAndConversationsDecodeBackendResponses() throws {
        let jobJSON = """
        {"id":"f176e559-3ad0-4eae-9160-44f382f30dd3","friend_id":"8c6f8694-eece-4e0a-84e8-819c8e1ff62b",
         "recipient_igsid":"mock-igsid-1001","recipient_name":"Kenji Tanaka","scheduled_at":"2026-12-01T20:00:00+08:00",
         "time_zone_id":"Asia/Kuala_Lumpur","recurrence":"once","recurrence_title":"One time","rrule":null,
         "instruction":"ask if he's free this weekend","style":"close_friend","automation_enabled":true,
         "consent_recorded":true,"eligibility":"outside_messaging_window","status":"scheduled","job_status":"active",
         "last_executed_at":"2026-11-11T05:01:15.004821+00:00","last_run_status":"blocked",
         "latest_run_id":"923a6fbc-af32-4227-a420-9488a2a88545","failure_reason":"Blocked: outside standard window",
         "draft_text":"yo free this weekend?","created_via":"chat","some_future_field":1}
        """
        let job: ScheduledJob = try JSONDecoderBox.decode(Data(jobJSON.utf8))
        XCTAssertEqual(job.recipientIGSID, "mock-igsid-1001")
        XCTAssertEqual(job.timeZoneID, "Asia/Kuala_Lumpur")
        XCTAssertEqual(job.style, .closeFriend)
        XCTAssertEqual(job.eligibility, .outsideMessagingWindow)
        XCTAssertEqual(job.lastResultNote, .blocked)
        var kl = Calendar(identifier: .gregorian)
        kl.timeZone = TimeZone(identifier: "Asia/Kuala_Lumpur")!
        XCTAssertEqual(kl.component(.hour, from: job.scheduledAt), 20)
        XCTAssertNotNil(job.friendID)
        XCTAssertNotNil(job.latestRunID)

        let convJSON = """
        {"conversations":[{"id":"8c6f8694-eece-4e0a-84e8-819c8e1ff62b","unread_count":0,
          "friend":{"id":"8c6f8694-eece-4e0a-84e8-819c8e1ff62b","igsid":"mock-igsid-1001","display_name":"Kenji Tanaka",
            "username":"kenji.t","style":"close_friend","language":"ja","ai_reply_enabled":true,"consent":"granted",
            "last_inbound_at":"2026-11-11T03:01:10.1+00:00","manual_override_until":null},
          "messages":[{"id":"5b0e8f3c-6f0a-4a43-8d5a-0a1d3c2b1a00","sender":"echo_scheduled","text":"yo",
            "sent_at":"2026-11-11T05:01:15.004821+00:00"}]}]}
        """
        struct Wrapper: Decodable { let conversations: [ConversationSummary] }
        let wrapper: Wrapper = try JSONDecoderBox.decode(Data(convJSON.utf8))
        XCTAssertEqual(wrapper.conversations[0].friend.displayName, "Kenji Tanaka")
        XCTAssertEqual(wrapper.conversations[0].messages[0].sender, .echoScheduled)

        let unknownStatus: ScheduledJob.Status = try JSONDecoderBox.decode(Data(#""brand_new_state""#.utf8))
        XCTAssertEqual(unknownStatus, .scheduled, "unknown values degrade gracefully")
    }

    func testChatActionDecodes() throws {
        let json = """
        {"id":"12fb00a0-f101-44be-85de-48d396f23ef0","role":"assistant","text":"On it!","language":"en",
         "created_at":"2026-11-11T11:05:57.322573+08:00","memory_updates":[],
         "actions":[{"type":"scheduled_job","job_id":"f176e559-3ad0-4eae-9160-44f382f30dd3",
           "recipient_name":"Kenji Tanaka","scheduled_at":"2026-11-12T12:00:00+00:00","time_zone_id":"Asia/Kuala_Lumpur",
           "recurrence_title":"One time","automation_enabled":true,"instruction":"ask if he's free"}]}
        """
        let message: ChatMessage = try JSONDecoderBox.decode(Data(json.utf8))
        XCTAssertTrue(message.actions[0].isScheduledJob)
        XCTAssertEqual(message.actions[0].recipientName, "Kenji Tanaka")
        XCTAssertNotNil(message.actions[0].formattedWhen)
        XCTAssertEqual(message.actions[0].scheduledAt?.timeIntervalSince1970,
                       ServerDate.parse("2026-11-12T20:00:00+08:00")?.timeIntervalSince1970)
    }

    func testInstagramStatusDecodes() throws {
        let json = """
        {"connected":true,"provider":"instagram","configured":true,"mock_account":false,"username":"my.creator",
         "account_type":"MEDIA_CREATOR","status":"active","token_expires_at":"2027-01-10T05:00:00.123456+00:00",
         "webhooks_subscribed":true,"last_webhook_at":null,"last_synced_at":null,"last_error":null}
        """
        let status: InstagramStatus = try JSONDecoderBox.decode(Data(json.utf8))
        XCTAssertTrue(status.connected)
        XCTAssertEqual(status.accountTypeTitle, "Creator")
        XCTAssertFalse(status.usesMockInstagram)
        XCTAssertNotNil(status.tokenExpiresAt)

        let notConfigured: InstagramStatus = try JSONDecoderBox.decode(Data(
            #"{"connected":false,"configured":false,"provider":"mock","mock_account":true}"#.utf8))
        XCTAssertTrue(notConfigured.usesMockInstagram)
    }

    func testConnectionModeResolution() {
        var settings = AppSettings()
        XCTAssertEqual(AppModel.resolveMode(settings: settings, token: "t"), .mock)
        settings.useMockServices = false
        XCTAssertEqual(AppModel.resolveMode(settings: settings, token: nil), .misconfigured("Save your API token to connect."))
        settings.backendBaseURL = "localhost:8000"
        XCTAssertEqual(AppModel.resolveMode(settings: settings, token: "t"), .misconfigured("The backend URL isn't valid."))
        settings.backendBaseURL = " http://192.168.1.20:8000/ "
        XCTAssertEqual(AppModel.resolveMode(settings: settings, token: "t"), .backend(URL(string: "http://192.168.1.20:8000")!))
    }
}

// MARK: - Phase 5: automatic replies

@MainActor
final class AutoReplyTests: XCTestCase {
    func testReviewItemDecodesServerJSON() throws {
        let json = """
        {"id": "7d1c1a3e-0000-4000-8000-000000000001", "origin": "auto_reply",
         "friend_id": "7d1c1a3e-0000-4000-8000-000000000002", "friend_name": "Mika Sato",
         "reason": "money", "reason_text": "mentions money or payments", "draft": "sorry, can't",
         "inbound_text": "lend me RM50?", "created_at": "2026-11-13T07:00:00Z",
         "window_expires_at": "2026-11-14T07:00:00Z", "status": "open"}
        """.data(using: .utf8)!
        let item: ReviewItem = try JSONDecoderBox.decode(json)
        XCTAssertEqual(item.origin, .autoReply)
        XCTAssertEqual(item.friendName, "Mika Sato")
        XCTAssertEqual(item.systemImage, "dollarsign.circle")
        XCTAssertTrue(item.windowOpen(now: ISO8601DateFormatter().date(from: "2026-11-13T08:00:00Z")!))
    }

    func testConversationDecodesLastAutoReply() throws {
        let json = """
        {"id": "7d1c1a3e-0000-4000-8000-000000000002", "unread_count": 0, "messages": [],
         "friend": {"id": "7d1c1a3e-0000-4000-8000-000000000002", "igsid": "x", "display_name": "Mika",
                    "username": "mika", "style": "close_friend", "language": "en", "ai_reply_enabled": true,
                    "consent": "granted", "last_inbound_at": null, "manual_override_until": null},
         "last_auto_reply": {"status": "skipped", "reason": "ai_off",
                             "text": "No reply: AI replies are off for this friend.", "at": "2026-11-15T10:00:00Z"}}
        """.data(using: .utf8)!
        let conversation: ConversationSummary = try JSONDecoderBox.decode(json)
        XCTAssertEqual(conversation.lastAutoReply?.status, "skipped")
        XCTAssertEqual(conversation.lastAutoReply?.systemImage, "minus.circle")
        let update: LiveUpdate = try JSONDecoderBox.decode(#"{"cursor": "2026-11-15T10:00:00+00:00", "changed": true}"#.data(using: .utf8)!)
        XCTAssertTrue(update.changed)
    }

    func testTakeoverChangesAutomationState() {
        var friend = Friend(id: UUID(), igsid: "x", displayName: "Mika", username: "mika", style: .closeFriend,
                            language: "en", aiReplyEnabled: true, consent: .granted, lastInboundAt: .now)
        var conversation = ConversationSummary(id: friend.id, friend: friend, messages: [], unreadCount: 0)
        XCTAssertEqual(conversation.automationState(globalPause: false), .active)
        friend.manualOverrideUntil = .now.addingTimeInterval(600)
        conversation.friend = friend
        XCTAssertEqual(conversation.automationState(globalPause: false), .takenOver)
        XCTAssertEqual(conversation.automationState(globalPause: false, pendingReviews: 1), .needsAttention)
        XCTAssertEqual(conversation.automationState(globalPause: true), .paused)
    }

    func testOfflineModeRepliesOrAsksYou() async throws {
        let store = MessagingStore.mock()
        guard let conversation = store.conversations.first else { return XCTFail("no sample data") }
        store.updateFriend(in: conversation.id) {
            $0.aiReplyEnabled = true
            $0.consent = .granted
        }
        try await Task.sleep(for: .milliseconds(50))
        await store.simulateInbound("how was the gig", in: conversation.id)
        XCTAssertEqual(store.conversation(id: conversation.id)?.messages.last?.sender, .echoAuto)

        await store.simulateInbound("can you lend me RM50", in: conversation.id)
        XCTAssertEqual(store.reviews.count, 1)
        let review = try XCTUnwrap(store.reviews.first)
        await store.resolveReview(review, action: .send, text: "sorry, can't this week")
        XCTAssertTrue(store.reviews.isEmpty)
        XCTAssertEqual(store.conversation(id: conversation.id)?.messages.last?.text, "sorry, can't this week")
    }
}

// MARK: - Phase 6

final class Phase6Tests: XCTestCase {
    /// Settings saved by older versions must still load — otherwise an update
    /// would silently reset the backend URL and switch ECHO back to mock mode.
    func testOldSettingsFileStillDecodes() throws {
        let old = """
        {"automationPaused": false, "backendBaseURL": "https://me.ngrok-free.dev", "useMockServices": false,
         "defaultTimeZoneID": "Asia/Kuala_Lumpur", "messageRetentionDays": 30,
         "notifications": {"messageSent": false, "jobBlocked": true, "deliveryFailed": true,
                           "needsAttention": true, "automationChanged": true, "connectionError": true,
                           "scheduleReminder": false}}
        """.data(using: .utf8)!
        let settings = try JSONDecoder().decode(AppSettings.self, from: old)
        XCTAssertEqual(settings.backendBaseURL, "https://me.ngrok-free.dev")
        XCTAssertFalse(settings.notifications.messageSent)
        XCTAssertFalse(settings.notifications.quietHoursEnabled)
        XCTAssertEqual(settings.notifications.quietStartMinutes, 23 * 60)
    }

    func testQuietHoursWireFormat() {
        var prefs = NotificationPreferences()
        prefs.quietHoursEnabled = true
        prefs.quietStartMinutes = 22 * 60 + 30
        prefs.quietEndMinutes = 6 * 60
        let wire = ServerNotificationPreferences(prefs)
        XCTAssertEqual(wire.quietHoursStart, "22:30:00")
        XCTAssertEqual(wire.quietHoursEnd, "06:00:00")
        XCTAssertEqual(wire.local, prefs)
        prefs.quietHoursEnabled = false
        XCTAssertNil(ServerNotificationPreferences(prefs).quietHoursStart)
    }

    @MainActor
    func testNotificationTapRoutes() {
        let push = PushManager.shared
        let id = UUID()
        push.handleTap(friendID: id.uuidString, category: "message_sent")
        XCTAssertEqual(push.route, .conversation(id))
        push.handleTap(friendID: id.uuidString, category: "needs_attention")
        XCTAssertEqual(push.route, .reviews)
        push.route = nil
    }
}

// MARK: - Smarter replies

@MainActor
final class SmarterRepliesTests: XCTestCase {
    func testReviewWithSuggestionsDecodesAndOlderServersStillWork() throws {
        let json = """
        {"id": "6f1c2a52-6f7a-4a8e-9a43-1b9b3f0d2a10", "origin": "auto_reply",
         "friend_id": "0b8a7f0e-6c8a-4f3c-9b1e-2a6a1d5c3e44", "friend_name": "Mika", "reason": "review_mode",
         "reason_text": "is waiting for your OK", "draft": "haha yes", "inbound_text": "[Reel: “cat”]",
         "created_at": "2026-11-16T09:00:00Z", "window_expires_at": "2026-11-17T09:00:00Z",
         "suggestions": [{"text": "haha yes", "kind": "text"}, {"text": "❤️", "kind": "reaction"}]}
        """
        let item: ReviewItem = try JSONDecoderBox.decode(Data(json.utf8))
        XCTAssertEqual(item.options.map(\.kind), [.text, .reaction])
        XCTAssertEqual(item.systemImage, "checkmark.bubble")

        let old = json.replacingOccurrences(of: #", "suggestions": [{"text": "haha yes", "kind": "text"}, {"text": "❤️", "kind": "reaction"}]"#, with: "")
        let oldItem: ReviewItem = try JSONDecoderBox.decode(Data(old.utf8))
        XCTAssertTrue(oldItem.options.isEmpty)
    }

    func testAutomationSettingsToleratesOlderServers() throws {
        let json = """
        {"paused": false, "takeover_minutes": 30, "daily_reply_limit": 50, "consent_request_text": "Is it ok?",
         "ai_disclosure_text": "AI may help me reply."}
        """
        let settings: AutomationSettings = try JSONDecoderBox.decode(Data(json.utf8))
        XCTAssertNil(settings.replyLength)
        let newer: AutomationSettings = try JSONDecoderBox.decode(Data(json.replacingOccurrences(
            of: "}", with: #", "auto_reply_mode": "review", "reply_length": "short", "learn_from_chats": true}"#).utf8))
        XCTAssertEqual(newer.autoReplyMode, .review)
        XCTAssertEqual(newer.replyLength, .short)
    }

    func testLearnedStyleDecodes() throws {
        let json = """
        {"tone": null, "formality": 2, "emoji_level": 2, "max_sentences": null, "slang_allowed": true,
         "custom_instructions": null, "avoid_topics": [], "learned_message_count": 12,
         "learned_at": "2026-11-16T09:00:00Z",
         "learned": {"tone": "playful", "favourite_emoji": ["😭"], "phrases": ["bro"], "typical_length": "short"}}
        """
        let style: FriendStyle = try JSONDecoderBox.decode(Data(json.utf8))
        XCTAssertEqual(style.learned?.phrases, ["bro"])
        XCTAssertEqual(style.learnedMessageCount, 12)
    }

    func testCalendarSummaryKeepsOnlyBusyBlocksAndOptionalTitles() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let raw = [
            ContextManager.RawEvent(start: now.addingTimeInterval(7200), end: now.addingTimeInterval(9000),
                                    allDay: false, free: false, cancelled: false, title: "Dentist"),
            ContextManager.RawEvent(start: now.addingTimeInterval(3600), end: now.addingTimeInterval(5400),
                                    allDay: false, free: true, cancelled: false, title: "Lunch?"),
            ContextManager.RawEvent(start: now, end: now.addingTimeInterval(600),
                                    allDay: false, free: false, cancelled: true, title: "Cancelled"),
        ]
        let private_ = ContextManager.summarise(raw, includeTitles: false)
        XCTAssertEqual(private_.count, 2)
        XCTAssertEqual(private_.map(\.busy), [false, true])            // sorted by start
        XCTAssertTrue(private_.allSatisfy { $0.title == nil })
        XCTAssertEqual(ContextManager.summarise(raw, includeTitles: true).last?.title, "Dentist")
    }

    func testContextUploadUsesServerKeys() throws {
        var upload = UserContextUpload()
        upload.location = .init(locality: "Petaling Jaya", region: "Selangor", country: "Malaysia")
        upload.clearCalendar = true
        let json = String(decoding: try APIClient.encoder.encode(upload), as: UTF8.self)
        XCTAssertTrue(json.contains("\"clear_calendar\":true"))
        XCTAssertTrue(json.contains("\"locality\":\"Petaling Jaya\""))
    }

    func testOfflineTapToSendAndReaction() async throws {
        let store = MessagingStore.mock()
        guard let conversation = store.conversations.first else { return XCTFail("no sample data") }
        store.updateFriend(in: conversation.id) {
            $0.aiReplyEnabled = true
            $0.consent = .granted
        }
        try await Task.sleep(for: .milliseconds(50))
        await store.simulateInbound("can you lend me RM50", in: conversation.id)
        var review = try XCTUnwrap(store.reviews.first)
        XCTAssertEqual(review.options.count, 3)

        await store.moreSuggestions(for: review)
        review = try XCTUnwrap(store.reviews.first)
        XCTAssertTrue(review.options.contains(.init(text: "❤️", kind: .reaction)))

        await store.resolveReview(review, action: .send, kind: .reaction)
        XCTAssertEqual(store.conversation(id: conversation.id)?.messages.last?.text, "❤️ reacted")
    }

    func testPartnerAndFamilyPresetsRoundTrip() throws {
        for preset in [Friend.StylePreset.partner, .family] {
            let data = try JSONEncoder().encode(preset)
            XCTAssertEqual(try JSONDecoder().decode(Friend.StylePreset.self, from: data), preset)
        }
    }
}
