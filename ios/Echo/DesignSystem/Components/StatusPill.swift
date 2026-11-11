import SwiftUI

/// Compact status capsule. Always pairs colour with an icon and a word, so
/// status is never communicated by colour alone.
struct StatusPill: View {
    let title: String
    let systemImage: String
    let tint: Color

    var body: some View {
        Label(title, systemImage: systemImage)
            .font(EchoFont.monoSmall)
            .textCase(.uppercase)
            .foregroundStyle(tint)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(tint.opacity(0.14), in: Capsule())
            .overlay(Capsule().strokeBorder(tint.opacity(0.4), lineWidth: 1))
            .fixedSize()
    }
}

extension StatusPill {
    init(status: ScheduledJob.Status) {
        self.init(title: status.title, systemImage: status.systemImage, tint: status.tint)
    }

    init(automation: AutomationState) {
        self.init(title: automation.title, systemImage: automation.systemImage, tint: automation.tint)
    }
}

/// Small rounded tag for traits, languages, presets.
struct TagChip: View {
    let text: String
    var tint: Color = EchoColor.cyan
    var systemImage: String?

    var body: some View {
        HStack(spacing: 4) {
            if let systemImage {
                Image(systemName: systemImage).imageScale(.small)
            }
            Text(text)
        }
        .font(EchoFont.footnote.weight(.medium))
        .foregroundStyle(EchoColor.textPrimary)
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(tint.opacity(0.14), in: Capsule())
        .overlay(Capsule().strokeBorder(tint.opacity(0.35), lineWidth: 1))
    }
}

/// Section header with a monospaced readout on the right.
struct SectionHeader: View {
    let title: String
    var readout: String?

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
                .font(EchoFont.headline)
                .foregroundStyle(EchoColor.textPrimary)
            Spacer()
            if let readout {
                Text(readout).echoReadout()
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

/// Initials avatar with a neon gradient ring.
struct AvatarView: View {
    let name: String
    var size: CGFloat = 44
    var active: Bool = false

    private var initials: String {
        let parts = name.split(separator: " ").prefix(2)
        let letters = parts.compactMap { $0.first }.map(String.init).joined()
        return letters.isEmpty ? "?" : letters.uppercased()
    }

    var body: some View {
        Text(initials)
            .font(.system(size: size * 0.36, weight: .semibold, design: .rounded))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(EchoColor.surfaceElevated, in: Circle())
            .overlay(
                Circle().strokeBorder(
                    active ? AnyShapeStyle(EchoColor.primaryGradient) : AnyShapeStyle(EchoColor.hairline),
                    lineWidth: 2
                )
            )
            .shadow(color: active ? EchoColor.neonPurple.opacity(0.45) : .clear, radius: 8)
            .accessibilityHidden(true)
    }
}
