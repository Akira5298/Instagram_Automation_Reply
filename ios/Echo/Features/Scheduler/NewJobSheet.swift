import SwiftUI

struct NewJobSheet: View {
    let viewModel: SchedulerViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var friendID: UUID?
    @State private var date = Date.now.addingTimeInterval(3600)
    @State private var recurrence: ScheduledJob.Recurrence = .once
    @State private var instruction = ""
    @State private var mode: Mode = .instruction
    @State private var exactText = ""
    @State private var occasion = ""
    @State private var ideas: [MessageIdea] = []
    @State private var loadingIdeas = false

    enum Mode: String, CaseIterable, Identifiable {
        case instruction, exact
        var id: String { rawValue }
        var title: String { self == .instruction ? "ECHO writes it" : "Exact message" }
    }
    @State private var automationEnabled = true
    @State private var isSaving = false
    @State private var saveError: String?

    private var friends: [Friend] { viewModel.store.friends }
    private var selectedFriend: Friend? { friends.first { $0.id == friendID } }

    private var canSave: Bool {
        selectedFriend != nil
            && !(mode == .instruction ? instruction : exactText).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && date > .now
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Recipient") {
                    Picker("Friend", selection: $friendID) {
                        Text("Choose…").tag(UUID?.none)
                        ForEach(friends) { friend in
                            Text(friend.displayName).tag(UUID?.some(friend.id))
                        }
                    }
                    if let friend = selectedFriend {
                        LabeledContent("Style", value: friend.style.title)
                        LabeledContent("Consent") {
                            Text(friend.consent.title).foregroundStyle(friend.consent.tint)
                        }
                    }
                }

                Section {
                    DatePicker("When", selection: $date, in: Date.now..., displayedComponents: [.date, .hourAndMinute])
                    Picker("Repeat", selection: $recurrence) {
                        ForEach(ScheduledJob.Recurrence.choices) { option in
                            Text(option.title).tag(option)
                        }
                    }
                } header: {
                    Text("Timing")
                } footer: {
                    Text("Times are in \(viewModel.timeZone.identifier). Repeats keep the same local time across daylight-saving changes.")
                }
                .environment(\.timeZone, viewModel.timeZone)

                Section {
                    Picker("Message", selection: $mode) {
                        ForEach(Mode.allCases) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    if mode == .instruction {
                        TextField("e.g. Ask if they're free this weekend", text: $instruction, axis: .vertical)
                            .lineLimit(2...5)
                    } else {
                        TextField("The exact words to send", text: $exactText, axis: .vertical)
                            .lineLimit(2...6)
                    }
                    Toggle("Send automatically", isOn: $automationEnabled)
                        .tint(EchoColor.neonPurple)
                } header: {
                    Text("What should it say?")
                } footer: {
                    Text(mode == .instruction
                         ? "ECHO writes it in your voice right before sending."
                         : "Sent word for word — no AI draft. Instagram's rules still apply.")
                }

                if let friend = selectedFriend {
                    Section {
                        HStack {
                            TextField("Occasion (optional): good morning, her exam…", text: $occasion)
                            Button {
                                Task {
                                    loadingIdeas = true
                                    ideas = await viewModel.ideas(for: friend, occasion: occasion)
                                    loadingIdeas = false
                                }
                            } label: {
                                if loadingIdeas { ProgressView() } else { Label("Suggest", systemImage: "sparkles") }
                            }
                            .disabled(loadingIdeas)
                        }
                        ForEach(ideas) { idea in
                            Button {
                                mode = .exact
                                exactText = idea.text
                                Haptics.select()
                            } label: {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(idea.text).foregroundStyle(EchoColor.textPrimary)
                                    Text(idea.vibe).echoReadout()
                                }
                            }
                        }
                    } header: {
                        Text("Ideas for \(friend.firstName)")
                    } footer: {
                        Text("Written the way you talk to \(friend.firstName) (\(friend.style.title.lowercased())). Tap one to use it, then edit if you like.")
                    }
                }

                if let saveError {
                    Section {
                        Label(saveError, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(EchoColor.danger)
                    }
                }

                Section {
                    Label {
                        Text("Scheduling isn't the same as being allowed to send. Right before sending, ECHO checks that \(selectedFriend?.displayName ?? "your friend") messaged you within the last 24 hours and agreed to AI-assisted messages. If not, the message is blocked, you're notified, and you can send it yourself.")
                    } icon: {
                        Image(systemName: "shield.lefthalf.filled").foregroundStyle(EchoColor.cyan)
                    }
                    .font(EchoFont.footnote)
                    .foregroundStyle(EchoColor.textSecondary)
                }
            }
            .scrollContentBackground(.hidden)
            .background(EchoColor.background)
            .navigationTitle("New message")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Schedule") {
                        guard let friend = selectedFriend else { return }
                        Task {
                            isSaving = true
                            let ok = await viewModel.createJob(
                                friend: friend,
                                date: date,
                                recurrence: recurrence,
                                instruction: mode == .instruction ? instruction : "",
                                automationEnabled: automationEnabled,
                                messageText: mode == .exact ? exactText : nil
                            )
                            isSaving = false
                            if ok {
                                Haptics.success()
                                dismiss()
                            } else {
                                saveError = viewModel.errorMessage
                                viewModel.errorMessage = nil
                            }
                        }
                    }
                    .disabled(!canSave || isSaving)
                }
            }
        }
    }
}
