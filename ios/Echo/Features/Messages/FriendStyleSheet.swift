import SwiftUI

/// How ECHO writes to one friend, on top of your general profile.
struct FriendStyleSheet: View {
    let store: MessagingStore
    let friend: Friend
    @Environment(\.dismiss) private var dismiss
    @State private var style = FriendStyle()
    @State private var topics = ""
    @State private var loaded = false
    @State private var saving = false

    var body: some View {
        NavigationStack {
            ZStack {
                EchoBackground()
                Form {
                    Section {
                        TextField("e.g. teasing, warm, to the point", text: Binding(
                            get: { style.tone ?? "" }, set: { style.tone = $0 }))
                        levelRow("Formality", value: $style.formality, labels: FriendStyle.formalityLevels)
                        levelRow("Emoji", value: $style.emojiLevel, labels: FriendStyle.levels)
                        Stepper(value: Binding(
                            get: { style.maxSentences ?? 0 },
                            set: { style.maxSentences = $0 == 0 ? nil : $0 }), in: 0...6) {
                            LabeledContent("Max sentences", value: style.maxSentences.map(String.init) ?? "No limit")
                        }
                        Toggle("Slang OK", isOn: $style.slangAllowed)
                    } header: {
                        Text("Tone")
                    }

                    Section {
                        TextField("e.g. call her Mik, reply in Malay", text: Binding(
                            get: { style.customInstructions ?? "" }, set: { style.customInstructions = $0 }),
                                  axis: .vertical)
                            .lineLimit(2...5)
                        TextField("Topics to avoid, separated by commas", text: $topics)
                    } header: {
                        Text("Rules for \(friend.firstName)")
                    } footer: {
                        Text("ECHO follows these on top of your profile and the \(friend.style.title.lowercased()) preset. It still never makes promises or plans for you.")
                    }
                }
                .scrollContentBackground(.hidden)
                .tint(EchoColor.neonPurple)
                .disabled(!loaded)
                .overlay { if !loaded { ProgressView() } }
            }
            .navigationTitle("Reply style")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }.disabled(!loaded || saving)
                }
            }
            .task {
                if let loadedStyle = await store.style(for: friend) {
                    style = loadedStyle
                    topics = loadedStyle.avoidTopics.joined(separator: ", ")
                }
                loaded = true
            }
        }
    }

    private func levelRow(_ title: String, value: Binding<Int>, labels: [String]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            LabeledContent(title, value: labels[max(0, min(labels.count - 1, value.wrappedValue))])
            Slider(value: Binding(get: { Double(value.wrappedValue) }, set: { value.wrappedValue = Int($0.rounded()) }),
                   in: 0...Double(labels.count - 1), step: 1)
                .accessibilityValue(labels[max(0, min(labels.count - 1, value.wrappedValue))])
        }
    }

    private func save() {
        saving = true
        style.avoidTopics = topics.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        Task {
            if await store.saveStyle(style, for: friend) {
                Haptics.success()
                dismiss()
            }
            saving = false
        }
    }
}
