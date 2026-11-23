import SwiftUI

struct SchedulerView: View {
    @Bindable var viewModel: SchedulerViewModel
    @State private var showingNewJob = false
    @State private var showingActivity = false
    @State private var selectedJob: ScheduledJob?

    var body: some View {
        NavigationStack {
            ZStack {
                EchoBackground()
                ScrollView {
                    VStack(alignment: .leading, spacing: EchoSpacing.md) {
                        if viewModel.store.isMock {
                            Label("Offline sample data — connect your backend in Settings for real scheduling", systemImage: "testtube.2")
                                .echoReadout(EchoColor.warning)
                        }

                        WeekStrip(viewModel: viewModel)

                        Picker("Filter", selection: $viewModel.filter) {
                            ForEach(SchedulerViewModel.Filter.allCases) { filter in
                                Text(filter == .attention && viewModel.issueCount > 0
                                     ? "\(filter.title) (\(viewModel.issueCount))"
                                     : filter.title)
                                .tag(filter)
                            }
                        }
                        .pickerStyle(.segmented)

                        SectionHeader(
                            title: viewModel.selectedDay.map { viewModel.format($0, .dateTime.weekday(.wide).day().month()) } ?? "Timeline",
                            readout: "\(viewModel.visibleJobs.count) jobs · \(viewModel.timeZone.identifier)"
                        )

                        if viewModel.visibleJobs.isEmpty {
                            ContentUnavailableView(
                                "Nothing here",
                                systemImage: "calendar.badge.clock",
                                description: Text("Tap + to schedule a message, or ask ECHO in chat: “Send Kenji a message at 8 PM tomorrow asking if he’s free.”")
                            )
                        } else {
                            TimelineList(viewModel: viewModel) { selectedJob = $0 }
                        }
                    }
                    .padding(.horizontal, EchoSpacing.md)
                    .padding(.bottom, EchoSpacing.lg)
                }
                .refreshable { await viewModel.refresh() }
            }
            .navigationTitle("Scheduler")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        showingActivity = true
                    } label: {
                        Image(systemName: viewModel.store.unreadActivityCount > 0 ? "bell.badge.fill" : "bell")
                            .foregroundStyle(viewModel.store.unreadActivityCount > 0 ? EchoColor.warning : EchoColor.textSecondary)
                    }
                    .accessibilityLabel("Activity, \(viewModel.store.unreadActivityCount) unread")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showingNewJob = true
                    } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.title3)
                            .foregroundStyle(EchoColor.cyan)
                    }
                    .accessibilityLabel("New scheduled message")
                }
            }
            .sheet(isPresented: $showingNewJob) {
                NewJobSheet(viewModel: viewModel)
                    .presentationDetents([.large])
            }
            .sheet(isPresented: $showingActivity) {
                ActivitySheet(store: viewModel.store)
                    .presentationDetents([.medium, .large])
            }
            .sheet(item: $selectedJob) { job in
                JobDetailSheet(store: viewModel.store, jobID: job.id)
                    .presentationDetents([.large])
            }
            .alert(
                "Scheduler",
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

// MARK: - Week strip (calendar)

private struct WeekStrip: View {
    let viewModel: SchedulerViewModel

    var body: some View {
        HStack(spacing: 6) {
            ForEach(viewModel.weekDays(), id: \.self) { day in
                let selected = viewModel.selectedDay.map { viewModel.calendar.isDate($0, inSameDayAs: day) } ?? false
                let count = viewModel.jobCount(on: day)
                Button {
                    Haptics.select()
                    withAnimation(.snappy) { viewModel.select(day: day) }
                } label: {
                    VStack(spacing: 6) {
                        Text(viewModel.format(day, .dateTime.weekday(.narrow)))
                            .font(EchoFont.monoSmall)
                            .foregroundStyle(EchoColor.textTertiary)
                        Text(viewModel.format(day, .dateTime.day()))
                            .font(EchoFont.headline)
                            .foregroundStyle(selected ? .white : EchoColor.textPrimary)
                        Circle()
                            .fill(count > 0 ? EchoColor.cyan : .clear)
                            .frame(width: 5, height: 5)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(selected ? AnyShapeStyle(EchoColor.primaryGradient) : AnyShapeStyle(EchoColor.surface.opacity(0.6)))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .strokeBorder(selected ? .white.opacity(0.25) : EchoColor.hairline, lineWidth: 1)
                    )
                    .shadow(color: selected ? EchoColor.neonPurple.opacity(0.5) : .clear, radius: 10)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(viewModel.format(day, .dateTime.weekday(.wide).day().month(.wide))), \(count) scheduled")
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
    }
}

// MARK: - Timeline

private struct TimelineList: View {
    let viewModel: SchedulerViewModel
    let onSelect: (ScheduledJob) -> Void

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(viewModel.visibleJobs.enumerated()), id: \.element.id) { index, job in
                HStack(alignment: .top, spacing: EchoSpacing.sm) {
                    VStack(spacing: 0) {
                        Circle()
                            .fill(job.status.tint)
                            .frame(width: 10, height: 10)
                            .shadow(color: job.status.tint.opacity(0.8), radius: 5)
                            .padding(.top, 20)
                        Rectangle()
                            .fill(index == viewModel.visibleJobs.count - 1 ? .clear : EchoColor.hairline)
                            .frame(width: 2)
                    }
                    .frame(width: 12)
                    .accessibilityHidden(true)

                    JobCard(job: job, viewModel: viewModel)
                        .padding(.bottom, EchoSpacing.sm)
                        .contentShape(Rectangle())
                        .onTapGesture { onSelect(job) }
                        .accessibilityAction(named: "Details") { onSelect(job) }
                }
            }
        }
    }
}

struct JobCard: View {
    let job: ScheduledJob
    let viewModel: SchedulerViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: EchoSpacing.xs) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(job.formattedTime)
                        .font(EchoFont.title)
                        .foregroundStyle(EchoColor.textPrimary)
                    Text("\(job.formattedDate) · \(job.recurrence.title)")
                        .echoReadout()
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 4) {
                    StatusPill(status: job.status)
                    if let last = job.lastResultNote {
                        Text("Last: \(last.title)")
                            .font(EchoFont.monoSmall)
                            .foregroundStyle(last.tint)
                    }
                }
            }

            Label(job.recipientName, systemImage: "person.fill")
                .font(EchoFont.headline)
                .foregroundStyle(EchoColor.textPrimary)

            Text(job.messageText.map { "“\($0)”" } ?? job.instruction)
                .font(EchoFont.callout)
                .foregroundStyle(EchoColor.textSecondary)

            HStack(spacing: 6) {
                Image(systemName: job.eligibility == .eligible ? "checkmark.shield" : "shield.lefthalf.filled")
                Text(job.eligibility.explanation)
            }
            .font(EchoFont.caption)
            .foregroundStyle(job.eligibility == .outsideMessagingWindow ? EchoColor.blocked : EchoColor.textTertiary)

            if let reason = job.failureReason {
                Label(reason, systemImage: "info.circle")
                    .font(EchoFont.caption)
                    .foregroundStyle((job.lastResultNote ?? job.status).tint)
            }

            if !job.automationEnabled {
                Label("Draft only — ECHO asks you before sending", systemImage: "hand.raised")
                    .font(EchoFont.caption)
                    .foregroundStyle(EchoColor.textTertiary)
            }
        }
        .glassCard(glow: job.status.isIssue ? job.status.tint : nil)
        .contextMenu {
            if job.status == .scheduled || job.status == .paused || job.status == .retryScheduled {
                Button(job.status == .paused ? "Resume" : "Pause", systemImage: job.status == .paused ? "play" : "pause") {
                    Task { await viewModel.togglePause(job) }
                }
            }
            if job.status != .cancelled && job.status != .paused {
                Button("Run now (test)", systemImage: "bolt") {
                    Task { await viewModel.runNow(job) }
                }
            }
            if job.status.isUpcoming || job.status == .needsAttention {
                Button("Cancel job", systemImage: "xmark.circle", role: .destructive) {
                    Task { await viewModel.cancel(job) }
                }
            }
            Button("Remove from list", systemImage: "trash", role: .destructive) {
                Task { await viewModel.delete(job) }
            }
        }
        .accessibilityHint("Double tap for details. Touch and hold for actions.")
    }
}

#Preview {
    SchedulerView(viewModel: SchedulerViewModel(store: .mock()))
        .preferredColorScheme(.dark)
}
