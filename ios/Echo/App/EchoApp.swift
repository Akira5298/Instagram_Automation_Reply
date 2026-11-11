import SwiftUI
import UIKit

@main
struct EchoApp: App {
    @State private var app = AppModel()

    init() {
        // iOS 26+ draws the native Liquid Glass tab bar, which already fits the
        // theme. Older versions get a dark frosted bar to match.
        if #unavailable(iOS 26) {
            let appearance = UITabBarAppearance()
            appearance.configureWithTransparentBackground()
            appearance.backgroundEffect = UIBlurEffect(style: .systemUltraThinMaterialDark)
            appearance.backgroundColor = UIColor(EchoColor.background.opacity(0.55))
            UITabBar.appearance().standardAppearance = appearance
            UITabBar.appearance().scrollEdgeAppearance = appearance
        }
    }

    var body: some Scene {
        WindowGroup {
            RootTabView(app: app)
                .preferredColorScheme(.dark)
                .fontDesign(.rounded)
        }
    }
}
