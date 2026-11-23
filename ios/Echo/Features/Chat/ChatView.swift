import SwiftUI

struct ChatView: View {
    @Bindable var viewModel: ChatViewModel
    var onOpenScheduler: () -> Void = {}

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var showingMemory = false
    @State private var confirmingClear = false
    @FocusState private var inputFocused: Bool

    private let bottomID = "bottom"

    var body: some View {
        NavigationStack {
            ZStack {
                EchoBackground()

                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: EchoSpacing.sm) {
                            ForEach(viewModel.messages) { message in
                                MessageBubble(
                                    message: message,
                                    onRetry: { Task { await viewModel.retry(message) } },
                                    onSaveExample: { Task { await viewModel.saveAsExample(message) } },
                                    onOpenAction: { _ in onOpenScheduler() }
                                )
                                .transition(messageTransition(for: message))
                            }
                            if viewModel.isAwaitingReply {
                                HStack {
                                    TypingIndicator()
                                    Spacer()
                                }
                                .transition(.opacity)
                            }
                            Color.clear.frame(height: 1).id(bottomID)
                        }
                        .padding(.horizontal, EchoSpacing.md)
                        .padding(.top, EchoSpacing.sm)
                        .animation(reduceMotion ? nil : .spring(response: 0.4, dampingFraction: 0.82), value: viewModel.messages.count)
                        .animation(.easeInOut(duration: 0.2), value: viewModel.isAwaitingReply)
                    }
                    .scrollDismissesKeyboard(.interactively)
                    .onAppear { proxy.scrollTo(bottomID, anchor: .bottom) }
                    .onChange(of: viewModel.messages.count) {
                        scrollToBottom(proxy)
                    }
                    .onChange(of: viewModel.isAwaitingReply) {
                        scrollToBottom(proxy)
                    }
                    .onChange(of: inputFocused) {
                        if inputFocused { scrollToBottom(proxy) }
                    }
                }
            }
            .safeAreaInset(edge: .top) {
                if let problem = viewModel.connectionProblem {
                    Button {
                        Task { await viewModel.load() }
                    } label: {
                        Label(problem, systemImage: "wifi.exclamationmark")
                            .font(EchoFont.caption.weight(.medium))
                            .foregroundStyle(EchoColor.warning)
                            .lineLimit(2)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, EchoSpacing.md)
                            .padding(.vertical, EchoSpacing.xs)
                            .background(.ultraThinMaterial)
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Tap to try again")
                }
            }
            .overlay(alignment: .top) {
                if let toast = viewModel.toast {
                    Label(toast, systemImage: "checkmark.circle.fill")
                        .font(EchoFont.callout.weight(.semibold))
                        .foregroundStyle(EchoColor.success)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 10)
                        .background(.ultraThinMaterial, in: Capsule())
                        .padding(.top, EchoSpacing.xs)
                        .transition(.move(edge: .top).combined(with: .opacity))
                        .task {
                            try? await Task.sleep(for: .seconds(2))
                            withAnimation { viewModel.toast = nil }
                        }
                }
            }
            .animation(.easeInOut(duration: 0.25), value: viewModel.toast)
            .refreshable { await viewModel.load() }
            .safeAreaInset(edge: .bottom) {
                ChatInputBar(
                    text: $viewModel.draft,
                    canSend: viewModel.canSend,
                    isFocused: $inputFocused
                ) {
                    Haptics.tap()
                    Task { await viewModel.send() }
                }
            }
            .navigationTitle("ECHO")
            .navigationBarTitleDisplayMode(.inline)
            // TabView keeps this screen alive on other tabs; a text field that stays
            // focused off-screen makes UIKit send keyboard notifications for a
            // keyboard that isn't shown.
            .onDisappear { inputFocused = false }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    if viewModel.isRemote {
                        Text("Claude").echoReadout(EchoColor.cyan)
                            .accessibilityLabel("Connected to your backend")
                    } else {
                        Text("Mock").echoReadout(EchoColor.warning)
                            .accessibilityLabel("Running with mock AI")
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    HStack(spacing: EchoSpacing.sm) {
                        MemoryIndicator(count: viewModel.memoryCount, lastLearnedAt: viewModel.lastLearnedAt) {
                            inputFocused = false          // keyboard away before the sheet comes up
                            showingMemory = true
                        }
                        Menu {
                            Button("Clear conversation", systemImage: "trash", role: .destructive) {
                                inputFocused = false
                                confirmingClear = true
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                                .foregroundStyle(EchoColor.textSecondary)
                        }
                        .accessibilityLabel("More")
                    }
                }
            }
            .sheet(isPresented: $showingMemory) {
                RecentMemorySheet(updates: viewModel.recentLearnings, total: viewModel.memoryCount)
                    .presentationDetents([.medium, .large])
                    .presentationBackground(.ultraThinMaterial)
            }
            .confirmationDialog(
                "Clear this conversation?",
                isPresented: $confirmingClear,
                titleVisibility: .visible
            ) {
                Button("Clear conversation", role: .destructive) { Task { await viewModel.clearConversation() } }
            } message: {
                Text("Your learned profile is kept. Manage it in Personality.")
            }
            .alert(
                "Something went wrong",
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

    private func messageTransition(for message: ChatMessage) -> AnyTransition {
        if reduceMotion { return .opacity }
        let edge: Edge = message.role == .user ? .trailing : .leading
        return .asymmetric(
            insertion: .move(edge: edge).combined(with: .opacity).combined(with: .scale(scale: 0.96)),
            removal: .opacity
        )
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        if reduceMotion {
            proxy.scrollTo(bottomID, anchor: .bottom)
        } else {
            withAnimation(.easeOut(duration: 0.25)) { proxy.scrollTo(bottomID, anchor: .bottom) }
        }
    }
}

// MARK: - Input bar

struct ChatInputBar: View {
    @Binding var text: String
    let canSend: Bool
    var isFocused: FocusState<Bool>.Binding
    let onSend: () -> Void

    var body: some View {
        HStack(alignment: .bottom, spacing: EchoSpacing.xs) {
            TextField("Message ECHO…", text: $text, axis: .vertical)
                .lineLimit(1...5)
                .font(EchoFont.body)
                .foregroundStyle(EchoColor.textPrimary)
                .tint(EchoColor.cyan)
                .focused(isFocused)
                .submitLabel(.send)
                .onSubmit { if canSend { onSend() } }
                .padding(.horizontal, 16)
                .padding(.vertical, 11)
                .background(EchoColor.surfaceElevated.opacity(0.85), in: RoundedRectangle(cornerRadius: 22, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 22, style: .continuous)
                        .strokeBorder(isFocused.wrappedValue ? EchoColor.cyan.opacity(0.6) : EchoColor.hairline, lineWidth: 1)
                )

            Button(action: onSend) {
                Image(systemName: "arrow.up")
                    .font(.system(.body, design: .rounded).weight(.bold))
                    .foregroundStyle(.white)
                    .frame(width: 44, height: 44)
                    .background(
                        Circle().fill(canSend ? AnyShapeStyle(EchoColor.primaryGradient) : AnyShapeStyle(EchoColor.charcoal))
                    )
                    .shadow(color: canSend ? EchoColor.neonPurple.opacity(0.6) : .clear, radius: 10)
            }
            .disabled(!canSend)
            .accessibilityLabel("Send")
            .animation(.easeInOut(duration: 0.2), value: canSend)
        }
        .padding(.horizontal, EchoSpacing.md)
        .padding(.top, EchoSpacing.xs)
        .padding(.bottom, EchoSpacing.xs)
        .background(.ultraThinMaterial)
    }
}

// MARK: - Memory indicator

struct MemoryIndicator: View {
    let count: Int
    let lastLearnedAt: Date?
    let action: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pulse = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Image(systemName: "brain.head.profile")
                    .symbolEffect(.bounce, value: reduceMotion ? 0 : count)
                Text("\(count)")
                    .font(EchoFont.monoSmall)
                    .contentTransition(.numericText())
            }
            .foregroundStyle(EchoColor.cyan)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(EchoColor.cyan.opacity(pulse ? 0.28 : 0.12), in: Capsule())
            .overlay(Capsule().strokeBorder(EchoColor.cyan.opacity(0.45), lineWidth: 1))
            .shadow(color: EchoColor.cyan.opacity(pulse ? 0.6 : 0), radius: 8)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Memory: \(count) learned traits")
        .accessibilityHint("Shows what ECHO learned recently")
        .onChange(of: lastLearnedAt) {
            guard !reduceMotion else { return }
            withAnimation(.easeOut(duration: 0.3)) { pulse = true }
            withAnimation(.easeIn(duration: 0.8).delay(0.4)) { pulse = false }
        }
        .animation(.default, value: count)
    }
}

// MARK: - Recent memory sheet

struct RecentMemorySheet: View {
    let updates: [MemoryUpdate]
    let total: Int

    var body: some View {
        NavigationStack {
            List {
                Section {
                    if updates.isEmpty {
                        Text("Nothing yet — just chat naturally and ECHO will start noticing patterns.")
                            .foregroundStyle(EchoColor.textSecondary)
                    }
                    ForEach(updates) { update in
                        HStack(alignment: .top, spacing: EchoSpacing.sm) {
                            Image(systemName: update.category.systemImage)
                                .foregroundStyle(EchoColor.cyan)
                                .frame(width: 22)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(update.summary).foregroundStyle(EchoColor.textPrimary)
                                Text(update.category.title).font(EchoFont.caption).foregroundStyle(EchoColor.textTertiary)
                            }
                            Spacer()
                            Text(update.kind == .statedFact ? "Fact" : "Tentative")
                                .echoReadout(update.kind == .statedFact ? EchoColor.success : EchoColor.warning)
                        }
                        .listRowBackground(EchoColor.surface.opacity(0.6))
                    }
                } header: {
                    Text("Recently learned · \(total) traits total")
                } footer: {
                    Text("Tentative items are guesses from how you write. They only firm up with repeated evidence, and you can edit or delete anything in Personality.")
                }
            }
            .scrollContentBackground(.hidden)
            .navigationTitle("Memory")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

#Preview {
    let profile = ProfileStore(repository: LocalProfileRepository(
        store: LocalJSONStore(filename: "preview-profile.json", directory: .temporaryDirectory)
    ))
    return ChatView(
        viewModel: ChatViewModel(
            service: MockChatService(),
            profileStore: profile,
            store: LocalJSONStore(filename: "preview-chat.json", directory: .temporaryDirectory)
        )
    )
    .preferredColorScheme(.dark)
}
