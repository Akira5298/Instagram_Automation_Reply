import SwiftUI

struct PersonalityView: View {
    @Bindable var viewModel: PersonalityViewModel

    var body: some View {
        NavigationStack {
            ZStack {
                EchoBackground()
                ScrollView {
                    VStack(alignment: .leading, spacing: EchoSpacing.lg) {
                        StrengthCard(viewModel: viewModel)

                        if viewModel.profile.traits.isEmpty {
                            ContentUnavailableView(
                                "No profile yet",
                                systemImage: "brain.head.profile",
                                description: Text("Chat with ECHO and it will start learning how you write. Everything appears here for you to review.")
                            )
                        }

                        ForEach(viewModel.populatedCategories) { category in
                            CategorySection(category: category, viewModel: viewModel)
                        }

                        ExamplesSection(viewModel: viewModel)
                    }
                    .padding(.horizontal, EchoSpacing.md)
                    .padding(.bottom, EchoSpacing.xl)
                }
                .refreshable { await viewModel.refresh() }
            }
            .navigationTitle("Personality")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("Add something about me", systemImage: "plus") {
                            viewModel.isAddingTrait = true
                        }
                        Button("Export profile (JSON)", systemImage: "square.and.arrow.up") {
                            Task { await viewModel.prepareExport() }
                        }
                        Divider()
                        Button("Delete everything learned", systemImage: "trash", role: .destructive) {
                            viewModel.confirmingDeleteAll = true
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle").foregroundStyle(EchoColor.cyan)
                    }
                    .accessibilityLabel("Profile actions")
                }
            }
            .sheet(item: $viewModel.editingTrait) { trait in
                TraitEditorSheet(trait: trait) { edited in Task { await viewModel.save(edited) } }
            }
            .sheet(isPresented: $viewModel.isAddingTrait) {
                TraitEditorSheet(trait: nil) { new in Task { await viewModel.add(category: new.category, value: new.value) } }
            }
            .confirmationDialog(
                "Delete your whole profile?",
                isPresented: $viewModel.confirmingDeleteAll,
                titleVisibility: .visible
            ) {
                Button("Delete everything", role: .destructive) {
                    Task { await viewModel.deleteAll() }
                }
            } message: {
                Text(viewModel.isRemote
                     ? "ECHO forgets every learned trait and writing example on your server. This can't be undone."
                     : "ECHO forgets every learned trait and writing example. This can't be undone.")
            }
            .sheet(item: $viewModel.export) { export in
                ExportSheet(json: export.json)
            }
            .alert(
                "Couldn't update your profile",
                isPresented: Binding(
                    get: { viewModel.errorMessage != nil },
                    set: { if !$0 { viewModel.errorMessage = nil } }
                )
            ) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(viewModel.errorMessage ?? "")
            }
        }
    }
}

private struct StrengthCard: View {
    let viewModel: PersonalityViewModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: EchoSpacing.md) {
            ZStack {
                Circle().stroke(EchoColor.hairline, lineWidth: 8)
                Circle()
                    .trim(from: 0, to: viewModel.profileStrength)
                    .stroke(EchoColor.primaryGradient, style: StrokeStyle(lineWidth: 8, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .shadow(color: EchoColor.neonPurple.opacity(0.6), radius: 6)
                    .animation(reduceMotion ? nil : .spring(response: 0.8), value: viewModel.profileStrength)
                Text("\(Int(viewModel.profileStrength * 100))%")
                    .font(EchoFont.headline)
                    .foregroundStyle(EchoColor.textPrimary)
                    .contentTransition(.numericText())
            }
            .frame(width: 76, height: 76)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Profile strength \(Int(viewModel.profileStrength * 100)) percent")

            VStack(alignment: .leading, spacing: 6) {
                Text("Your communication style").font(EchoFont.headline).foregroundStyle(EchoColor.textPrimary)
                Text("\(viewModel.confirmedCount) confirmed · \(viewModel.tentativeCount) tentative")
                    .echoReadout()
                if !viewModel.languages.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(viewModel.languages) { trait in
                                TagChip(text: trait.value.replacingOccurrences(of: "Writes in ", with: ""), systemImage: "globe")
                            }
                        }
                    }
                }
            }
        }
        .glassCard(glow: EchoColor.neonPurple, cornerRadius: EchoRadius.lg)
    }
}

private struct CategorySection: View {
    let category: ProfileCategory
    let viewModel: PersonalityViewModel

    var body: some View {
        let traits = viewModel.profile.traits(in: category)
        VStack(alignment: .leading, spacing: EchoSpacing.xs) {
            Label(category.title, systemImage: category.systemImage)
                .font(EchoFont.headline)
                .foregroundStyle(EchoColor.textPrimary)
                .accessibilityAddTraits(.isHeader)

            VStack(spacing: 0) {
                ForEach(traits) { trait in
                    TraitRow(trait: trait)
                        .contentShape(Rectangle())
                        .onTapGesture { viewModel.editingTrait = trait }
                        .contextMenu {
                            Button("Edit", systemImage: "pencil") { viewModel.editingTrait = trait }
                            Button("Delete", systemImage: "trash", role: .destructive) {
                                Task { await viewModel.delete(trait) }
                            }
                        }
                        .accessibilityAction(named: "Edit") { viewModel.editingTrait = trait }
                        .accessibilityAction(named: "Delete") { Task { await viewModel.delete(trait) } }
                    if trait.id != traits.last?.id {
                        Divider().overlay(EchoColor.hairline)
                    }
                }
            }
            .glassCard(padding: EchoSpacing.sm)
        }
    }
}

private struct TraitRow: View {
    let trait: ProfileTrait

    var body: some View {
        HStack(spacing: EchoSpacing.sm) {
            VStack(alignment: .leading, spacing: 3) {
                Text(trait.value)
                    .font(EchoFont.body)
                    .foregroundStyle(trait.isTentative ? EchoColor.textSecondary : EchoColor.textPrimary)
                HStack(spacing: 6) {
                    Text(trait.source.label)
                    if trait.source == .inferred {
                        Text("· \(Int(trait.confidence * 100))% · seen \(trait.evidenceCount)×")
                    }
                }
                .echoReadout(trait.isTentative ? EchoColor.warning : EchoColor.textTertiary)
            }
            Spacer()
            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(EchoColor.textTertiary)
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .combine)
        .accessibilityHint(trait.isTentative ? "Tentative guess. Double tap to edit." : "Double tap to edit.")
    }
}

/// Approved writing examples (style references for drafting messages).
private struct ExamplesSection: View {
    let viewModel: PersonalityViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: EchoSpacing.xs) {
            Label("Writing examples", systemImage: "text.badge.star")
                .font(EchoFont.headline)
                .foregroundStyle(EchoColor.textPrimary)
                .accessibilityAddTraits(.isHeader)

            if viewModel.examples.isEmpty {
                Text("In AI Chat, touch and hold one of your messages and choose “Use as writing example”. ECHO uses these as references for how you really text.")
                    .font(EchoFont.callout)
                    .foregroundStyle(EchoColor.textSecondary)
                    .glassCard(padding: EchoSpacing.sm)
            } else {
                VStack(spacing: 0) {
                    ForEach(viewModel.examples) { example in
                        HStack(alignment: .top) {
                            Text("“\(example.text)”")
                                .font(EchoFont.body)
                                .foregroundStyle(EchoColor.textPrimary)
                            Spacer()
                            if let language = example.language {
                                Text(language).echoReadout()
                            }
                        }
                        .padding(.vertical, 8)
                        .contextMenu {
                            Button("Remove example", systemImage: "trash", role: .destructive) {
                                Task { await viewModel.deleteExample(example) }
                            }
                        }
                        .accessibilityAction(named: "Remove example") {
                            Task { await viewModel.deleteExample(example) }
                        }
                        if example.id != viewModel.examples.last?.id {
                            Divider().overlay(EchoColor.hairline)
                        }
                    }
                }
                .glassCard(padding: EchoSpacing.sm)
            }
        }
    }
}

private struct ExportSheet: View {
    let json: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                Text(json)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(EchoColor.textSecondary)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding()
            }
            .background(EchoColor.background)
            .navigationTitle("Profile export")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
                ToolbarItem(placement: .primaryAction) {
                    ShareLink(item: json, preview: SharePreview("ECHO profile export")) {
                        Image(systemName: "square.and.arrow.up")
                    }
                }
            }
        }
    }
}

/// Edit an existing trait, or create a new one when `trait` is nil.
struct TraitEditorSheet: View {
    let trait: ProfileTrait?
    let onSave: (ProfileTrait) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var value = ""
    @State private var category: ProfileCategory = .interests

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Category", selection: $category) {
                        ForEach(ProfileCategory.allCases) { category in
                            Label(category.title, systemImage: category.systemImage).tag(category)
                        }
                    }
                    TextField("What should ECHO know?", text: $value, axis: .vertical)
                        .lineLimit(1...4)
                } footer: {
                    Text("Edits are saved as confirmed. ECHO won't overwrite them with guesses.")
                }
            }
            .scrollContentBackground(.hidden)
            .background(EchoColor.background)
            .navigationTitle(trait == nil ? "Add to profile" : "Edit trait")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        var updated = trait ?? ProfileTrait(
                            category: category, key: "", value: "", source: .userEdited, confidence: 1
                        )
                        updated.category = category
                        updated.value = value.trimmingCharacters(in: .whitespacesAndNewlines)
                        onSave(updated)
                        dismiss()
                    }
                    .disabled(value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .onAppear {
                if let trait {
                    value = trait.value
                    category = trait.category
                }
            }
        }
        .presentationDetents([.medium])
    }
}

#Preview {
    let repository = LocalProfileRepository(
        store: LocalJSONStore(filename: "preview-profile.json", directory: .temporaryDirectory)
    )
    let store = ProfileStore(repository: repository, initial: repository.profile)
    return PersonalityView(viewModel: PersonalityViewModel(profileStore: store))
        .task { await store.didLearn(MockStyleAnalyzer().analyze("ngl I love bouldering lol 😂").updates) }
        .preferredColorScheme(.dark)
}
