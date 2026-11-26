# Pmax AI — Android Personal Assistant

## Features in this starter build
- Bilingual Burmese + English chat using Gemini API.
- Smart assistant persona.
- Optional Google Search grounding via Gemini's `google_search` tool (availability/model/API access depends on Google account and current API support).
- Voice input using Android speech recognition intent.
- Voice playback can be added/extended with Android TextToSpeech; this MVP currently includes voice input and chat.
- Local conversation history and editable local memory in app settings.
- API key stored in app-private SharedPreferences on this device.

## Setup
1. Install the APK.
2. Open Settings (gear icon).
3. Add your Gemini API key. Keep it private; do not share it or commit it to GitHub.
4. Save a short memory if desired.
5. Chat in Burmese or English. Tap the microphone for voice input (device speech recognition and language support vary).

Get an API key through Google's official AI developer platform. Review current availability, quotas, pricing, and terms.

## Build using GitHub Actions
Copy `PmaxAI/` into the root of your GitHub repository and copy `.github/workflows/build-pmax-ai.yml` into the repository `.github/workflows/` directory. Commit and push. Open GitHub Actions and download artifact `Pmax-AI-debug-apk` from a successful run.

## Limitations and security
- This is an MVP starter, not a fully autonomous agent. It does not control the phone or execute arbitrary commands.
- API calls send prompts and recent chat context directly to Google's Gemini API over HTTPS. Review Google's data and API terms.
- The API key is stored locally in app-private preferences, but a rooted/compromised device can expose app data. For a public release, use a secure backend and per-user authentication rather than embedding keys in a distributed app.
- Voice recognition relies on the speech service installed on the Android device. Burmese recognition may not be available on every device.
