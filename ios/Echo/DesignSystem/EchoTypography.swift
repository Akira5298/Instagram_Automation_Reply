import SwiftUI

/// Type scale built on Dynamic Type text styles, so every size scales with the
/// user's accessibility text size. Rounded SF for headings, SF Mono for the
/// small "system readout" labels that give ECHO its cyberpunk flavour.
enum EchoFont {
    static let display = Font.system(.largeTitle, design: .rounded).weight(.bold)
    static let title = Font.system(.title2, design: .rounded).weight(.semibold)
    static let headline = Font.system(.headline, design: .rounded)
    static let body = Font.system(.body)
    static let callout = Font.system(.callout)
    static let footnote = Font.system(.footnote)
    static let caption = Font.system(.caption)
    /// Uppercase technical labels: "MEMORY · 12 TRAITS"
    static let mono = Font.system(.caption, design: .monospaced).weight(.medium)
    static let monoSmall = Font.system(.caption2, design: .monospaced).weight(.semibold)
}

enum EchoSpacing {
    static let xxs: CGFloat = 4
    static let xs: CGFloat = 8
    static let sm: CGFloat = 12
    static let md: CGFloat = 16
    static let lg: CGFloat = 24
    static let xl: CGFloat = 32
}

enum EchoRadius {
    static let sm: CGFloat = 10
    static let md: CGFloat = 18
    static let lg: CGFloat = 26
}

extension View {
    /// Small uppercase monospaced label style.
    func echoReadout(_ color: Color = EchoColor.textTertiary) -> some View {
        self.font(EchoFont.mono)
            .textCase(.uppercase)
            .tracking(1.2)
            .foregroundStyle(color)
    }
}
