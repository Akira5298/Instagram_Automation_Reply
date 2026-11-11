import SwiftUI

struct NeonButtonStyle: ButtonStyle {
    enum Kind { case primary, secondary, destructive }

    var kind: Kind = .primary
    var fullWidth: Bool = true

    func makeBody(configuration: Configuration) -> some View {
        NeonButtonBody(configuration: configuration, kind: kind, fullWidth: fullWidth)
    }
}

private struct NeonButtonBody: View {
    let configuration: ButtonStyleConfiguration
    let kind: NeonButtonStyle.Kind
    let fullWidth: Bool

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        configuration.label
            .font(EchoFont.headline)
            .foregroundStyle(.white)
            .padding(.vertical, 14)
            .padding(.horizontal, 22)
            .frame(maxWidth: fullWidth ? .infinity : nil)
            .background(fill, in: Capsule())
            .overlay(Capsule().strokeBorder(glowColor.opacity(0.6), lineWidth: 1))
            .shadow(color: glowColor.opacity(configuration.isPressed ? 0.15 : 0.4), radius: configuration.isPressed ? 6 : 14)
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.97 : 1)
            .opacity(isEnabled ? 1 : 0.45)
            .animation(reduceMotion ? nil : .spring(response: 0.25, dampingFraction: 0.7), value: configuration.isPressed)
    }

    private var fill: AnyShapeStyle {
        switch kind {
        case .primary: return AnyShapeStyle(EchoColor.primaryGradient)
        case .secondary: return AnyShapeStyle(EchoColor.surfaceElevated)
        case .destructive: return AnyShapeStyle(EchoColor.danger.opacity(0.85))
        }
    }

    private var glowColor: Color {
        switch kind {
        case .primary: return EchoColor.neonPurple
        case .secondary: return EchoColor.cyan
        case .destructive: return EchoColor.danger
        }
    }
}

extension ButtonStyle where Self == NeonButtonStyle {
    static var neon: NeonButtonStyle { NeonButtonStyle() }

    static func neon(_ kind: NeonButtonStyle.Kind, fullWidth: Bool = true) -> NeonButtonStyle {
        NeonButtonStyle(kind: kind, fullWidth: fullWidth)
    }
}

#Preview {
    VStack(spacing: 16) {
        Button("Primary") {}.buttonStyle(.neon)
        Button("Secondary") {}.buttonStyle(.neon(.secondary))
        Button("Pause all automation") {}.buttonStyle(.neon(.destructive))
    }
    .padding()
    .background(EchoColor.background)
    .preferredColorScheme(.dark)
}
