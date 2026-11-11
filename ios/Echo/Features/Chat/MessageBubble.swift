import SwiftUI
import UIKit

struct MessageBubble: View {
    let message: ChatMessage
    var onRetry: (() -> Void)?
    var onSaveExample: (() -> Void)?
    var onOpenAction: ((ChatAction) -> Void)?

    private var isUser: Bool { message.role == .user }

    var body: some View {
        HStack(alignment: .bottom, spacing: EchoSpacing.xs) {
            if isUser { Spacer(minLength: 48) }

            VStack(alignment: isUser ? .trailing : .leading, spacing: 6) {
                Text(message.text)
                    .font(EchoFont.body)
                    .foregroundStyle(EchoColor.textPrimary)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(bubbleBackground)
                    .overlay(bubbleShape.stroke(borderColor, lineWidth: 1))
                    .shadow(color: isUser ? EchoColor.neonPurple.opacity(0.35) : .clear, radius: 10, y: 4)
                    .opacity(message.deliveryState == .sending ? 0.7 : 1)
                    .contextMenu {
                        Button("Copy", systemImage: "doc.on.doc") {
                            UIPasteboard.general.string = message.text
                        }
                        if isUser, message.deliveryState == nil, let onSaveExample {
                            Button("Use as writing example", systemImage: "text.badge.star", action: onSaveExample)
                        }
                    }
                    .accessibilityLabel(Text("\(isUser ? "You" : "ECHO"): \(message.text)"))

                if message.deliveryState == .failed, let onRetry {
                    Button(action: onRetry) {
                        Label("Not delivered · Tap to retry", systemImage: "exclamationmark.arrow.circlepath")
                            .font(EchoFont.caption.weight(.semibold))
                            .foregroundStyle(EchoColor.danger)
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Sends this message again")
                }

                ForEach(Array(message.actions.enumerated()), id: \.offset) { _, action in
                    ChatActionCard(action: action) { onOpenAction?(action) }
                }

                if !message.memoryUpdates.isEmpty {
                    LearnedSummary(updates: message.memoryUpdates)
                }
            }

            if !isUser { Spacer(minLength: 48) }
        }
    }

    private var bubbleShape: UnevenRoundedRectangle {
        UnevenRoundedRectangle(
            topLeadingRadius: 20,
            bottomLeadingRadius: isUser ? 20 : 6,
            bottomTrailingRadius: isUser ? 6 : 20,
            topTrailingRadius: 20,
            style: .continuous
        )
    }

    @ViewBuilder private var bubbleBackground: some View {
        if isUser {
            bubbleShape.fill(EchoColor.userBubbleGradient)
        } else {
            bubbleShape.fill(.ultraThinMaterial)
                .overlay(bubbleShape.fill(EchoColor.surfaceElevated.opacity(0.7)))
        }
    }

    private var borderColor: Color {
        isUser ? .white.opacity(0.18) : EchoColor.cyan.opacity(0.22)
    }
}

/// "＋ learned 2 things" line under ECHO's replies, expandable. Observations
/// are labelled tentative so guesses never look like facts.
private struct LearnedSummary: View {
    let updates: [MemoryUpdate]
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button {
                withAnimation(.snappy) { expanded.toggle() }
            } label: {
                Label(
                    "Learned \(updates.count) \(updates.count == 1 ? "thing" : "things")",
                    systemImage: "brain.head.profile"
                )
                .echoReadout(EchoColor.cyan.opacity(0.85))
            }
            .buttonStyle(.plain)
            .accessibilityHint(expanded ? "Hide details" : "Show what ECHO learned")

            if expanded {
                ForEach(updates) { update in
                    HStack(spacing: 6) {
                        Image(systemName: update.category.systemImage)
                            .foregroundStyle(EchoColor.cyan)
                        Text(update.summary)
                            .foregroundStyle(EchoColor.textSecondary)
                        Text(update.isRemoval ? "removed" : (update.kind == .statedFact ? "fact" : "tentative"))
                            .font(EchoFont.monoSmall)
                            .foregroundStyle(update.isRemoval ? EchoColor.textTertiary
                                             : (update.kind == .statedFact ? EchoColor.success : EchoColor.warning))
                    }
                    .font(EchoFont.caption)
                }
                .transition(.opacity)
            }
        }
        .padding(.leading, 4)
    }
}

#Preview {
    VStack(spacing: 12) {
        MessageBubble(message: ChatMessage(role: .user, text: "ngl I love bouldering lol"))
        MessageBubble(message: ChatMessage(
            role: .assistant,
            text: "Nice — bouldering sounds fun. What got you into it?",
            memoryUpdates: MockStyleAnalyzer().analyze("ngl I love bouldering lol").updates
        ))
    }
    .padding()
    .background(EchoColor.background)
    .preferredColorScheme(.dark)
}

/// Confirmation card for something ECHO did, e.g. scheduling a message.
struct ChatActionCard: View {
    let action: ChatAction
    let onOpen: () -> Void

    var body: some View {
        if action.isScheduledJob {
            Button(action: onOpen) {
                HStack(alignment: .top, spacing: EchoSpacing.sm) {
                    Image(systemName: "calendar.badge.clock")
                        .font(.title3)
                        .foregroundStyle(EchoColor.cyan)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Scheduled for \(action.recipientName ?? "your friend")")
                            .font(EchoFont.headline)
                            .foregroundStyle(EchoColor.textPrimary)
                        if let when = action.formattedWhen {
                            Text("\(when) · \(action.recurrenceTitle ?? "One time")")
                                .echoReadout(EchoColor.cyan)
                        }
                        if let instruction = action.instruction, !instruction.isEmpty {
                            Text(instruction)
                                .font(EchoFont.caption)
                                .foregroundStyle(EchoColor.textSecondary)
                        }
                        Text(action.automationEnabled == false
                             ? "Draft only — ECHO will ask you before sending."
                             : "Sends automatically if Instagram allows it at that time.")
                            .font(EchoFont.caption)
                            .foregroundStyle(EchoColor.textTertiary)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.caption)
                        .foregroundStyle(EchoColor.textTertiary)
                }
                .glassCard(glow: EchoColor.cyan, padding: EchoSpacing.sm)
            }
            .buttonStyle(.plain)
            .accessibilityHint("Opens the Scheduler")
        } else if action.type == "schedule_failed" {
            Label(action.reason ?? "Couldn't schedule that.", systemImage: "exclamationmark.triangle.fill")
                .font(EchoFont.caption.weight(.medium))
                .foregroundStyle(EchoColor.warning)
                .padding(EchoSpacing.sm)
                .background(EchoColor.warning.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
        }
    }
}
