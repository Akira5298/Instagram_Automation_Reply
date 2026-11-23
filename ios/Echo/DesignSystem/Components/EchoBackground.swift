import SwiftUI

/// Deep-black backdrop with slowly drifting neon light. Static when the user
/// has Reduce Motion on.
struct EchoBackground: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var drift = false

    var body: some View {
        ZStack {
            EchoColor.background
            glow
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
        .accessibilityHidden(true)
        // Start the drift *after* the view is on screen, in its own transaction.
        // The old `withAnimation(.repeatForever)` inside `.onAppear` ran in the same
        // transaction as the navigation push / sheet / tab switch that made the
        // view appear, so SwiftUI could attach the never-ending animation to that
        // layout change too (screens sliding or drifting, repeated layout passes).
        .task {
            guard !reduceMotion, !drift else { return }
            drift = true
        }
    }

    /// The animation is scoped to the glow circles only, via `.animation(_:value:)`.
    private var glow: some View {
        ZStack {
            Circle()
                .fill(EchoColor.neonPurple.opacity(0.32))
                .frame(width: 380, height: 380)
                .blur(radius: 130)
                .offset(x: drift ? 120 : -110, y: drift ? -240 : -150)

            Circle()
                .fill(EchoColor.electricBlue.opacity(0.26))
                .frame(width: 340, height: 340)
                .blur(radius: 130)
                .offset(x: drift ? -110 : 130, y: drift ? 260 : 190)

            Circle()
                .fill(EchoColor.cyan.opacity(0.10))
                .frame(width: 220, height: 220)
                .blur(radius: 100)
                .offset(x: drift ? 70 : -50, y: drift ? 40 : -30)
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 16).repeatForever(autoreverses: true), value: drift)
    }
}

#Preview {
    EchoBackground().preferredColorScheme(.dark)
}
