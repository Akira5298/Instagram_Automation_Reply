import SwiftUI

/// What ECHO remembers about one friend. Only approved, unexpired memories are
/// used when ECHO writes to them. Sensitive ones always expire (30 days by default).
struct FriendMemoriesSheet: View {
    let store: MessagingStore
    let friend: Friend
    @Environment(\.dismiss) private var dismiss
    @State private var memories: [FriendMemory] = []
    @State private var draft = ""
    @State private var sensitive = false
    @State private var loaded = false

    private var waiting: [FriendMemory] { memories.filter { !$0.approved } }
    private var approved: [FriendMemory] { memories.filter(\.approved) }

    var body: some View {
        NavigationStack {
            ZStack {
                EchoBackground()
                List {
                    Section {
                        TextField("e.g. loves spicy ramen, has exams in December", text: $draft, axis: .vertical)
                            .lineLimit(1...3)
                        Toggle("Sensitive (health, family, money…)", isOn: $sensitive)
                        Button("Remember this") { add() }
                            .disabled(draft.trimmingCharacters(in: .whitespaces).count < 2)
                    } header: {
                        Text("Add")
                    } footer: {
                        Text("Sensitive memories are forgotten automatically after 30 days.")
                    }
                    .listRowBackground(EchoColor.surface.opacity(0.7))

                    if !waiting.isEmpty {
                        Section("Waiting for your OK") {
                            ForEach(waiting) { memory in
                                row(memory)
                                    .swipeActions(edge: .leading) {
                                        Button("Approve") { setApproved(memory, true) }.tint(EchoColor.success)
                                    }
                            }
                            .onDelete { delete(waiting, at: $0) }
                        }
                        .listRowBackground(EchoColor.surface.opacity(0.7))
                    }

                    Section {
                        if approved.isEmpty && loaded {
                            Text("Nothing yet. ECHO only knows what you add here.")
                                .foregroundStyle(EchoColor.textTertiary)
                        }
                        ForEach(approved) { memory in
                            row(memory)
                                .swipeActions(edge: .leading) {
                                    Button("Pause") { setApproved(memory, false) }.tint(EchoColor.warning)
                                }
                        }
                        .onDelete { delete(approved, at: $0) }
                    } header: {
                        Text("ECHO uses")
                    } footer: {
                        Text("Swipe left to forget, right to pause. ECHO never shares these with \(friend.firstName) word for word.")
                    }
                    .listRowBackground(EchoColor.surface.opacity(0.7))
                }
                .scrollContentBackground(.hidden)
                .overlay { if !loaded { ProgressView() } }
            }
            .navigationTitle("About \(friend.firstName)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .task {
                memories = await store.memories(for: friend)
                loaded = true
            }
        }
    }

    private func row(_ memory: FriendMemory) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(memory.content).font(EchoFont.body)
            HStack(spacing: EchoSpacing.xs) {
                if memory.sensitivity == .sensitive {
                    TagChip(text: "Sensitive", tint: EchoColor.warning, systemImage: "lock.fill")
                }
                if let expires = memory.expiresAt {
                    Text("Forgotten \(expires, format: .relative(presentation: .named))")
                        .font(EchoFont.caption)
                        .foregroundStyle(EchoColor.textTertiary)
                }
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }

    private func add() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        Task {
            if let created = await store.addMemory(text, sensitive: sensitive, for: friend) {
                memories.insert(created, at: 0)
                draft = ""
                sensitive = false
                Haptics.success()
            }
        }
    }

    private func setApproved(_ memory: FriendMemory, _ value: Bool) {
        var changed = memory
        changed.approved = value
        Task {
            if let saved = await store.updateMemory(changed), let index = memories.firstIndex(where: { $0.id == saved.id }) {
                memories[index] = saved
            }
        }
    }

    private func delete(_ list: [FriendMemory], at offsets: IndexSet) {
        let doomed = offsets.map { list[$0] }
        memories.removeAll { item in doomed.contains { $0.id == item.id } }
        Task { for memory in doomed { await store.deleteMemory(memory) } }
    }
}
