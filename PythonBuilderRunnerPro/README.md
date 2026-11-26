# Python Builder + Runner Pro (Android)

Android-only starter project using Java + Chaquopy Python 3.11.

## Features
- Python editor
- Import `.py` files using Android document picker
- Run Python code on-device
- Terminal output support (see note below)
- Included packages: requests, colorama, six
- GitHub Actions APK build

## Build
Copy this folder into the root of your GitHub repository, commit and push.
Open Actions, run **Build Python Builder Pro APK**, then download artifact
`Python-Builder-Runner-Pro-debug-apk`.

## Important MVP notes
- This is a starter MVP, not a full general-purpose Python IDE.
- Python scripts run inside Chaquopy; some desktop-only packages and native extensions
  are not compatible with Android.
- Package additions require editing `app/build.gradle` and rebuilding.
- The Build tab opens the repository's GitHub Actions page. It does not compile arbitrary
  scripts inside the phone; GitHub Actions performs the APK build.
- The current terminal display is a minimal placeholder; see source comments before
  treating this as production-ready. Extend output persistence/UI before release.
