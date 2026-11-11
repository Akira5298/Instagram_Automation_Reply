import SwiftUI

/// Frosted-glass card with a soft luminous border.
struct GlassCardModifier: ViewModifier {
    var glow: Color?
    var cornerRadius: CGFloat = EchoRadius.md
    var padding: CGFloat = EchoSpacing.md

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        return content
            .padding(padding)
            .background(
                ZStack {
                    shape.fill(.ultraThinMaterial)
                    shape.fill(EchoColor.surface.opacity(0.6))
                }
            )
            .overlay(
                shape.strokeBorder(
                    LinearGradient(
                        colors: [(glow ?? .white).opacity(glow == nil ? 0.14 : 0.55), .white.opacity(0.04)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ),
                    lineWidth: 1
                )
            )
            .shadow(color: (glow ?? .clear).opacity(0.22), radius: 18, y: 8)
    }
}

extension View {
    func glassCard(glow: Color? = nil, cornerRadius: CGFloat = EchoRadius.md, padding: CGFloat = EchoSpacing.md) -> some View {
        modifier(GlassCardModifier(glow: glow, cornerRadius: cornerRadius, padding: padding))
    }
}

#Preview {
    ZStack {
        EchoBackground()
        VStack(alignment: .leading, spacing: 8) {
            Text("Glass card").font(EchoFont.headline)
            Text("Frosted surface with a luminous edge.").font(EchoFont.callout)
                .foregroundStyle(EchoColor.textSecondary)
        }
        .glassCard(glow: EchoColor.neonPurple)
        .padding()
    }
    .preferredColorScheme(.dark)
}
