# ECHO on TestFlight + push notifications

*Phase 6. Apple details checked November 2026.*

TestFlight lets you install ECHO on your iPhone, and on a few trusted people's phones, without Xcode. It and push notifications both need the **Apple Developer Program** (paid yearly).

> **Push is switched OFF by default** so ECHO builds on a free Apple account. Turning it on once you have the paid program takes two steps: in `ios/project.yml`, uncomment the 4 `entitlements:` lines and set `ECHO_PUSH_ENABLED: "YES"`. Then run `xcodegen generate`. Until then, the app hides the push controls and never asks iOS for a push token.

---

## 1. One-time Apple setup

1. Enrol at developer.apple.com/programs.
2. Pick your bundle id, e.g. `com.akira.echo`.
   - Replace `com.yourname.echo` (and `com.yourname.echo.tests`) in `ios/project.yml`.
   - Set `DEVELOPMENT_TEAM` to your Team ID (Membership details), or pick the team in Xcode.
3. Run `cd ios && xcodegen generate`, then open the project. In **Signing & Capabilities**, *Automatically manage signing* registers the App ID with **Push Notifications** for you (it's in `Config/Echo.entitlements`).

## 2. Run with push from Xcode (sandbox)

Builds run from Xcode use APNs' **sandbox**. The app tells your server which environment it's in, so you don't have to.

1. Do §3 to give your Mac the APNs key.
2. Run ECHO on your iPhone. Push doesn't work in the Simulator for real APNs pushes.
3. Go to **Settings → Notifications → Turn on push notifications** and allow.
4. Tap **Send test**. A notification titled "ECHO test" should arrive within a few seconds.

## 3. The APNs key (your Mac's permission to push)

1. Go to developer.apple.com → **Certificates, IDs & Profiles → Keys → +**.
2. Name it "ECHO push", tick **Apple Push Notifications service (APNs)**, then Continue → Register.
3. **Download the `.p8` file.** Apple lets you download it only once. Keep it outside the project folder, e.g. `~/keys/AuthKey_ABC123DEFG.p8`, then run `chmod 600 ~/keys/*.p8`.
4. Add to `backend/.env`:
   ```bash
   APNS_KEY_PATH=/Users/<you>/keys/AuthKey_ABC123DEFG.p8
   APNS_KEY_ID=ABC123DEFG          # shown next to the key
   APNS_TEAM_ID=XYZ987TEAM         # Membership details → Team ID
   APNS_BUNDLE_ID=com.akira.echo   # exactly your app's bundle id
   ```
5. Restart `./scripts/start.sh`. Doctor's **Push notifications ✅** line confirms the key loads and signs.

One key works for both sandbox and production, and for all your apps. It doesn't expire. Revoke it in the same place if it ever leaks.

**What gets pushed:** whatever your server would also put in the activity feed, filtered by Settings → Notifications and silenced during quiet hours:
- "Replied to Mika"
- "Mika needs your reply"
- blocked or failed sends
- connection errors

Tapping a notification opens that chat, or the **Needs you** queue.

## 4. App Store Connect record

1. Go to appstoreconnect.apple.com → **Apps → + → New App**.
   - **Platform:** iOS
   - **Name:** something unique on the App Store (e.g. "ECHO – Akira", since "ECHO" is probably taken)
   - **Bundle ID:** yours
   - **SKU:** anything
2. That's all TestFlight needs. Store listing, screenshots and pricing are only for an App Store release.

## 5. Archive and upload

1. In Xcode, choose **Any iOS Device (arm64)** as the destination, then **Product → Archive**.
2. In the Organizer, choose **Distribute App → TestFlight & App Store** (or **TestFlight Internal Only**) → Upload.
   - Xcode switches `aps-environment` to production for this build, and the app registers as `production`.
3. **For every later upload, bump the build number first.** It's `CURRENT_PROJECT_VERSION` in `ios/project.yml` (then run `xcodegen generate`), or the Build field in Xcode. Raise `MARKETING_VERSION` (1.0.0 → 1.0.1) for meaningful releases.
4. **Export compliance:** already answered. `ITSAppUsesNonExemptEncryption = NO` is in `Config/Echo-Info.plist`, because ECHO uses only HTTPS and the iOS Keychain.
5. **Privacy manifest:** `Echo/Resources/PrivacyInfo.xcprivacy`. It declares no tracking, and only user content plus (if you turn on **Your day**) coarse, city-level location, both used for app functionality and sent to your own server. The location and calendar permission prompts only appear when you switch those on in Settings.

Processing takes from a few minutes to about an hour. You'll get an email.

## 6. Testers

From Apple's documentation, and worth knowing before you plan:
- **Internal testers:** up to 100 people on your App Store Connect team. No review is needed. You can turn on automatic distribution of new builds.
- **External testers:** up to 10,000 by email or public link. The first build added to an external group goes to **TestFlight App Review**.
- **Every build stops working after 90 days.** Upload a new build to keep testing.

For ECHO, internal testing is the natural fit: you, plus at most a couple of people you trust. External review would ask what the app does with other people's messages.

## 7. Using the TestFlight build

- **Backend URL:** your fixed ngrok address (`https://<name>.ngrok-free.dev`). The TestFlight build can't reach `localhost`.
- **API token:** the same `ECHO_API_TOKEN`. It must be the long random one: requests through the tunnel are refused while the token is weak (see SECURITY.md).
- Your Mac still has to be awake and running `start.sh` (and ngrok) for ECHO to work.

## 8. Checklist before each upload

- [ ] `python -m pytest -q` passes in `backend/`
- [ ] ⌘U passes in Xcode
- [ ] Build number bumped
- [ ] Doctor is all ✅ (or only optional ⚠️)
- [ ] Privacy page (`backend/app/api/instagram.py`) has your real name and contact

Sources:
- [TestFlight overview — App Store Connect Help](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/)
- [Add internal testers](https://developer.apple.com/help/app-store-connect/test-a-beta-version/add-internal-testers/)
- [TestFlight](https://developer.apple.com/testflight/)
