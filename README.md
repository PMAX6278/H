# MikroTik Ultimate Manager — Android project

This is an Android Studio project that packages the existing MikroTik Ultimate Manager V2 Flask dashboard inside an Android WebView. The Python/Flask backend runs locally inside the app through Chaquopy; it connects directly to RouterOS API on the LAN.

## Included
- Router overview (identity, RouterOS version, CPU, uptime)
- Simple Queue speed control
- Hotspot voucher generation and deletion
- Active Hotspot users and usage
- Confirmation dialogs on every router-changing form (existing V2 behavior)
- CSV download for the latest voucher batch
- First-launch router/app credential setup

## Build APK
1. Open this folder in Android Studio (Ladybug or newer).
2. Install Android SDK Platform 35 and a compatible JDK (17).
3. Allow Gradle sync and dependency downloads. Chaquopy downloads Python packages during the build.
4. Select **Build > Build Bundle(s) / APK(s) > Build APK(s)**.
5. Android Studio writes the debug APK under `app/build/outputs/apk/debug/`.

## First launch
Enter router address (default `10.10.10.254`), API port `8728`, RouterOS username/password, and a new dashboard password (at least 8 characters). The app stores these settings locally in Android app preferences. Use a dedicated RouterOS API account; do not use the full admin account for routine operation if you can avoid it.

## Important
- This source package is not itself a compiled APK. It must be built with Android Studio/Android SDK.
- RouterOS API must be enabled and reachable from the phone. For API-SSL, enable TLS on the router and use the appropriate port.
- The current dashboard uses RouterOS API plaintext login by default when API is selected. Keep the router API restricted to trusted LAN addresses; never expose it to the public internet.
- Every router-changing form in the dashboard asks for confirmation before submitting.

## Build without a computer (Android phone only)
This project includes a GitHub Actions workflow at `.github/workflows/android-apk.yml`.
1. On your Android phone, create/sign in to a GitHub account and create a **public or private repository**.
2. Upload the contents of this project folder to the repository (the `.github/workflows` folder must be included).
3. Open the repository's **Actions** tab and select **Build Android APK**. If prompted, enable workflows, then tap **Run workflow**.
4. Wait for the run to finish successfully. Open the completed run, find **Artifacts**, and download `MikroTik-Ultimate-Manager-debug-apk`.
5. Extract the downloaded artifact ZIP and install the APK. Android may ask you to allow installs from your browser or file manager.

The workflow builds a debug APK for testing. It does not sign a release APK for Play Store distribution.
