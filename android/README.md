# EvoSys Field - Android app

The field app (`https://app.evosyspro.live/wholesale/appointments`) as an installable
Android app. It is a Trusted Web Activity: the app opens the web field app
full-screen using the phone's Chrome, so alerts, saving with no signal and the
login all behave exactly as they do on the web. One build, two ways out:

- `app-release.apk` - installed straight onto a rep's phone (no store).
- `app-release.aab` - uploaded to Google Play when the Play account is approved.

Built by `.github/workflows/android-field.yml` on GitHub's servers (Java and the
Android tools are already there). The signing key is NOT in this repo: it is in
the repo's Actions secrets (`ANDROID_KEYSTORE_B64`, `ANDROID_KEYSTORE_PASSWORD`)
with a backup on Mike's PC (`C:\Dev\keys\`). Losing it means reps have to
uninstall and reinstall to get an update - keep the backup.

`frontend/public/.well-known/assetlinks.json` tells Android that this app and
the website belong together (that is what hides the browser address bar). It
must list the SHA-256 fingerprint of every key that signs the app - ours, and
Google Play's once the app is on Play.
