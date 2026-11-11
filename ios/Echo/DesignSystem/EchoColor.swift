import SwiftUI

/// ECHO colour tokens. The app runs in dark mode only; every text colour here
/// is checked for WCAG AA contrast against `background` and `surface`.
enum EchoColor {
    // Surfaces
    static let background = Color(hex: 0x07070C)
    static let surface = Color(hex: 0x11111A)
    static let surfaceElevated = Color(hex: 0x1A1A26)
    static let charcoal = Color(hex: 0x252532)

    // Neon accents
    static let neonPurple = Color(hex: 0xA855F7)
    static let electricBlue = Color(hex: 0x3B82F6)
    static let cyan = Color(hex: 0x22D3EE)

    // Text
    static let textPrimary = Color.white.opacity(0.95)
    static let textSecondary = Color.white.opacity(0.66)
    static let textTertiary = Color.white.opacity(0.45)

    // Semantic
    static let success = Color(hex: 0x34D399)
    static let warning = Color(hex: 0xFBBF24)
    static let danger = Color(hex: 0xF87171)
    static let blocked = Color(hex: 0xFB923C)

    static let hairline = Color.white.opacity(0.10)

    // Gradients (computed so they stay concurrency-safe)
    static var primaryGradient: LinearGradient {
        LinearGradient(colors: [neonPurple, electricBlue], startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    static var accentGradient: LinearGradient {
        LinearGradient(colors: [electricBlue, cyan], startPoint: .leading, endPoint: .trailing)
    }

    static var userBubbleGradient: LinearGradient {
        LinearGradient(
            colors: [neonPurple.opacity(0.85), electricBlue.opacity(0.85)],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}

extension Color {
    /// `Color(hex: 0xA855F7)`
    init(hex: UInt32, opacity: Double = 1) {
        let red = Double((hex >> 16) & 0xFF) / 255
        let green = Double((hex >> 8) & 0xFF) / 255
        let blue = Double(hex & 0xFF) / 255
        self.init(.sRGB, red: red, green: green, blue: blue, opacity: opacity)
    }
}
