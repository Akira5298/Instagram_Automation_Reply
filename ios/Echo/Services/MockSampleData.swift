import Foundation

/// Sample friends, threads and jobs for offline (mock) mode.
extension MockMessagingBackend {
    static func sample(now: Date = .now) -> (conversations: [ConversationSummary], jobs: [ScheduledJob]) {
        let tz = TimeZone(identifier: "Asia/Kuala_Lumpur") ?? .current
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = tz

        func at(days: Int, hour: Int, minute: Int = 0) -> Date {
            let start = calendar.startOfDay(for: now)
            let day = calendar.date(byAdding: .day, value: days, to: start) ?? start
            return calendar.date(bySettingHour: hour, minute: minute, second: 0, of: day) ?? day
        }

        let kenji = Friend(
            id: UUID(), igsid: "mock-igsid-1001", displayName: "Kenji Tanaka", username: "kenji.t",
            style: .closeFriend, language: "ja", aiReplyEnabled: true, consent: .granted,
            lastInboundAt: now.addingTimeInterval(-2 * 3600)
        )
        let giulia = Friend(
            id: UUID(), igsid: "mock-igsid-1002", displayName: "Giulia Rossi", username: "giulia.rossi",
            style: .classmate, language: "it", aiReplyEnabled: true, consent: .requested,
            lastInboundAt: now.addingTimeInterval(-30 * 3600)
        )
        let mei = Friend(
            id: UUID(), igsid: "mock-igsid-1003", displayName: "Mei Lin", username: "meilin_",
            style: .closeFriend, language: "zh-Hans", aiReplyEnabled: false, consent: .unknown,
            lastInboundAt: now.addingTimeInterval(-5 * 3600)
        )
        let daniel = Friend(
            id: UUID(), igsid: "mock-igsid-1004", displayName: "Daniel Ong", username: "daniel.ong.studio",
            style: .professional, language: "en", aiReplyEnabled: false, consent: .unknown,
            lastInboundAt: now.addingTimeInterval(-3 * 86_400)
        )

        let conversations = [
            ConversationSummary(id: UUID(), friend: kenji, messages: [
                InstagramMessage(sender: .friend, text: "おつかれ！週末なにしてる？", sentAt: now.addingTimeInterval(-3 * 3600)),
                InstagramMessage(sender: .echoAuto, text: "まだ決めてない笑 なんで？", sentAt: now.addingTimeInterval(-2.9 * 3600)),
                InstagramMessage(sender: .friend, text: "新しいラーメン屋行こうよ🍜", sentAt: now.addingTimeInterval(-2 * 3600)),
            ], unreadCount: 1),
            ConversationSummary(id: UUID(), friend: mei, messages: [
                InstagramMessage(sender: .friend, text: "明天的presentation准备好了吗？", sentAt: now.addingTimeInterval(-5 * 3600)),
            ], unreadCount: 1),
            ConversationSummary(id: UUID(), friend: giulia, messages: [
                InstagramMessage(sender: .friend, text: "Ciao! Hai gli appunti di ieri?", sentAt: now.addingTimeInterval(-30 * 3600)),
                InstagramMessage(sender: .me, text: "Sì, te li mando stasera!", sentAt: now.addingTimeInterval(-29 * 3600)),
            ], unreadCount: 0),
            ConversationSummary(id: UUID(), friend: daniel, messages: [
                InstagramMessage(sender: .friend, text: "Thanks for the shoot last week — sending the invoice details soon.", sentAt: now.addingTimeInterval(-3 * 86_400)),
            ], unreadCount: 0),
        ]

        let jobs = [
            ScheduledJob(
                id: UUID(), recipientIGSID: kenji.igsid, recipientName: kenji.displayName,
                scheduledAt: at(days: 1, hour: 20), timeZoneID: tz.identifier, recurrence: .once,
                instruction: "Casually ask whether they're free this weekend",
                style: .closeFriend, automationEnabled: true, consentRecorded: true,
                eligibility: .checkedAtDispatch, status: .scheduled
            ),
            ScheduledJob(
                id: UUID(), recipientIGSID: giulia.igsid, recipientName: giulia.displayName,
                scheduledAt: at(days: 0, hour: 9), timeZoneID: tz.identifier, recurrence: .weekdays,
                instruction: "Remind her about the study group",
                style: .classmate, automationEnabled: true, consentRecorded: false,
                eligibility: .outsideMessagingWindow, status: .blocked,
                lastExecutedAt: at(days: 0, hour: 9),
                failureReason: "Last message from Giulia was over 24h ago"
            ),
            ScheduledJob(
                id: UUID(), recipientIGSID: mei.igsid, recipientName: mei.displayName,
                scheduledAt: at(days: -1, hour: 18, minute: 30), timeZoneID: tz.identifier, recurrence: .weekly,
                instruction: "Wish good luck before Monday's presentation",
                style: .closeFriend, automationEnabled: true, consentRecorded: true,
                eligibility: .eligible, status: .sent, lastExecutedAt: at(days: -1, hour: 18, minute: 30)
            ),
            ScheduledJob(
                id: UUID(), recipientIGSID: daniel.igsid, recipientName: daniel.displayName,
                scheduledAt: at(days: 2, hour: 10), timeZoneID: tz.identifier, recurrence: .once,
                instruction: "Politely follow up on the shoot invoice",
                style: .professional, automationEnabled: false, consentRecorded: false,
                eligibility: .checkedAtDispatch, status: .paused
            ),
            ScheduledJob(
                id: UUID(), recipientIGSID: kenji.igsid, recipientName: kenji.displayName,
                scheduledAt: at(days: 0, hour: 12, minute: 15), timeZoneID: tz.identifier, recurrence: .once,
                instruction: "Reply about splitting the concert tickets",
                style: .closeFriend, automationEnabled: true, consentRecorded: true,
                eligibility: .eligible, status: .needsAttention,
                failureReason: "Involves money — waiting for you"
            ),
        ]

        return (conversations, jobs)
    }
}
