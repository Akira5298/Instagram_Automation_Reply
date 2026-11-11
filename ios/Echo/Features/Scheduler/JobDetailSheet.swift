import SwiftUI
import UIKit

/// Job details + delivery history. For a paused or blocked message you can
/// copy the draft and send it yourself, approve it, or dismiss it.
struct JobDetailSheet: View {
    let store: MessagingStore
    let jobID: UUID

    @Environment(\.dismiss) private var dismiss
    @State private var runs: [JobRun] = []
    @State private var isLoading = true
    @State private var copiedRunID: UUID?

    private var job: ScheduledJob? { store.jobs.first { $0.id == jobID } }

    /// Universal link — opens the Instagram app's inbox when installed.
    private static let instagramInbox = URL(string: "https://www.instagram.com/direct/inbox/")!

    var body: some View {
        NavigationStack {
            ZStack {
                EchoBackground()
                if let job {
                    ScrollView {
                        VStack(alignment: .leading, spacing: EchoSpacing.lg) {
                            summary(job)
                            history
                        }
                        .padding(EchoSpacing.md)
                    }
                    .refreshable { await load() }
                } else {
                    ContentUnavailableView("Job not found", systemImage: "calendar.badge.exclamationmark")
                }
            }
            .navigationTitle(job?.recipientName ?? "Scheduled message")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
                if let job {
                    ToolbarItem(placement: .primaryAction) {
                        Menu {
                            if job.status != .cancelled && job.status != .paused {
                                Button("Run now (test)", systemImage: "bolt") {
                                    Task { await store.runNow(job); await load() }
                                }
                            }
                            if job.status == .scheduled || job.status == .paused {
                                Button(job.status == .paused ? "Resume" : "Pause",
                                       systemImage: job.status == .paused ? "play" : "pause") {
                                    Task { await store.togglePause(job) }
                                }
                            }
                            if job.status != .cancelled {
                                Button("Cancel job", systemImage: "xmark.circle", role: .destructive) {
                                    Task { await store.cancel(job) }
                                }
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                        }
                    }
                }
            }
            .task { await load() }
        }
    }

    private func summary(_ job: ScheduledJob) -> some View {
        VStack(alignment: .leading, spacing: EchoSpacing.sm) {
            HStack {
                Text("\(job.formattedDate), \(job.formattedTime)")
                    .font(EchoFont.title)
                    .foregroundStyle(EchoColor.textPrimary)
                Spacer()
                StatusPill(status: job.status)
            }
            Text("\(job.recurrence.title) · \(job.timeZoneID) · \(job.style.title)")
                .echoReadout()
            Text(job.instruction)
                .font(EchoFont.body)
                .foregroundStyle(EchoColor.textSecondary)
            Label(job.automationEnabled ? "Sends automatically when allowed" : "Draft only — waits for you",
                  systemImage: job.automationEnabled ? "bolt.fill" : "hand.raised")
                .font(EchoFont.caption)
                .foregroundStyle(EchoColor.textTertiary)
            Label(job.consentRecorded ? "Consent recorded" : "No recorded consent — ECHO won't auto-send",
                  systemImage: job.consentRecorded ? "checkmark.shield" : "exclamationmark.shield")
                .font(EchoFont.caption)
                .foregroundStyle(job.consentRecorded ? EchoColor.success : EchoColor.warning)
        }
        .glassCard(glow: EchoColor.neonPurple)
    }

    @ViewBuilder
    private var history: some View {
        SectionHeader(title: "Delivery history", readout: isLoading ? "loading…" : "\(runs.count) runs")
        if !isLoading && runs.isEmpty {
            Text("No attempts yet. The first one happens at the scheduled time — or use “Run now (test)”.")
                .font(EchoFont.callout)
                .foregroundStyle(EchoColor.textSecondary)
        }
        ForEach(runs) { run in
            RunRow(
                run: run,
                copied: copiedRunID == run.id,
                onCopy: {
                    UIPasteboard.general.string = run.draftText
                    copiedRunID = run.id
                    Haptics.success()
                },
                onAction: { action in
                    Task {
                        await store.resolve(run, action: action)
                        await load()
                    }
                },
                instagramURL: Self.instagramInbox
            )
        }
    }

    private func load() async {
        guard let job else { return }
        isLoading = true
        runs = await store.runs(for: job)
        isLoading = false
    }
}

private struct RunRow: View {
    let run: JobRun
    let copied: Bool
    let onCopy: () -> Void
    let onAction: (JobRun.Action) -> Void
    let instagramURL: URL

    var body: some View {
        VStack(alignment: .leading, spacing: EchoSpacing.xs) {
            HStack {
                Text(run.scheduledFor, format: .dateTime.day().month(.abbreviated).hour().minute())
                    .font(EchoFont.headline)
                    .foregroundStyle(EchoColor.textPrimary)
                if run.attempt > 1 {
                    Text("attempt \(run.attempt)").echoReadout()
                }
                Spacer()
                StatusPill(status: run.status)
            }
            if let reason = run.reasonText {
                Text(reason.capitalizedFirst)
                    .font(EchoFont.caption)
                    .foregroundStyle(run.status.tint)
            }
            if let draft = run.draftText {
                Text("“\(draft)”")
                    .font(EchoFont.callout)
                    .foregroundStyle(EchoColor.textSecondary)
                    .padding(EchoSpacing.sm)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(EchoColor.surfaceElevated.opacity(0.7), in: RoundedRectangle(cornerRadius: 12))
            }
            if run.status == .needsAttention {
                HStack {
                    Button("Send anyway") { onAction(.approve) }
                        .buttonStyle(.neon(.primary, fullWidth: false))
                    Button("Dismiss") { onAction(.dismiss) }
                        .buttonStyle(.neon(.secondary, fullWidth: false))
                }
                Text("“Send anyway” still checks Instagram's 24-hour rule and consent.")
                    .font(EchoFont.caption)
                    .foregroundStyle(EchoColor.textTertiary)
            }
            if run.status == .blocked || run.status == .needsAttention || run.status == .failed, run.draftText != nil {
                HStack(spacing: EchoSpacing.md) {
                    Button(copied ? "Copied" : "Copy draft", systemImage: copied ? "checkmark" : "doc.on.doc", action: onCopy)
                    Link(destination: instagramURL) {
                        Label("Open Instagram", systemImage: "arrow.up.forward.app")
                    }
                    if run.status == .needsAttention {
                        Button("I sent it", systemImage: "checkmark.circle") { onAction(.sentManually) }
                    }
                }
                .font(EchoFont.callout.weight(.semibold))
                .foregroundStyle(EchoColor.cyan)
            }
        }
        .glassCard(glow: run.status.isIssue ? run.status.tint : nil, padding: EchoSpacing.sm)
    }
}

private extension String {
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
