import SwiftUI

struct NewJobSheet: View {
    let viewModel: SchedulerViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var friendID: UUID?
    @State private var date = Date.now.addingTimeInterval(3600)
    @State private var recurrence: ScheduledJob.Recurrence = .once
    @State private var instruction = ""
    @State private var automationEnabled = true
    @State private var isSaving = false
    @State private var saveError: String?

    private var friends: [Friend] { viewModel.store.friends }
    private var selectedFriend: Friend? { friends.first { $0.id == friendID } }

    private var canSave: Bool {
        selectedFriend != nil
            && !instruction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
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

                Section("What should ECHO say?") {
                    TextField("e.g. Ask if they're free this weekend", text: $instruction, axis: .vertical)
                        .lineLimit(2...5)
                    Toggle("Send automatically", isOn: $automationEnabled)
                        .tint(EchoColor.neonPurple)
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
                                instruction: instruction,
                                automationEnabled: automationEnabled
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
