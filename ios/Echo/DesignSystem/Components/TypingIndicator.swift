import SwiftUI

/// Three pulsing neon dots. Paused (static) under Reduce Motion.
struct TypingIndicator: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30.0, paused: reduceMotion)) { context in
            let time = context.date.timeIntervalSinceReferenceDate
            HStack(spacing: 6) {
                ForEach(0..<3, id: \.self) { index in
                    let phase = reduceMotion ? 0.6 : (sin(time * 5 - Double(index) * 0.8) + 1) / 2
                    Circle()
                        .fill(EchoColor.cyan)
                        .frame(width: 8, height: 8)
                        .opacity(0.35 + 0.65 * phase)
                        .scaleEffect(0.8 + 0.3 * phase)
                        .shadow(color: EchoColor.cyan.opacity(0.6 * phase), radius: 4)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
            .background(EchoColor.surfaceElevated.opacity(0.9), in: Capsule())
            .overlay(Capsule().strokeBorder(EchoColor.cyan.opacity(0.25), lineWidth: 1))
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("ECHO is typing")
    }
}

#Preview {
    TypingIndicator().padding().background(EchoColor.background)
}
