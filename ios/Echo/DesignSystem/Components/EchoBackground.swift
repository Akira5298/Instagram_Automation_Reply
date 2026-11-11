import SwiftUI

/// Deep-black backdrop with slowly drifting neon light. Static when the user
/// has Reduce Motion on.
struct EchoBackground: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var drift = false

    var body: some View {
        ZStack {
            EchoColor.background

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
        .ignoresSafeArea()
        .allowsHitTesting(false)
        .accessibilityHidden(true)
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 16).repeatForever(autoreverses: true)) {
                drift = true
            }
        }
    }
}

#Preview {
    EchoBackground().preferredColorScheme(.dark)
}
