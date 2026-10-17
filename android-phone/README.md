# Aemeath local Android companion

Receives the user's Android notifications with explicit notification-listener
access and sends them to the paired AI OFFICE PC over certificate-pinned HTTPS.
PC Ethernet and phone Wi-Fi can coexist on the same reachable LAN. No cloud relay,
SMS sender, messenger reply, accessibility service or desktop controller is included.

## Install and pair

1. Copy the locally built `aemeath-phone.apk` to the phone, for example by USB,
   and open it to install. Office also offers the file under Notifications → Phone
   connection. This download is local to the PC; it is not a public hosting URL.
2. On the PC, open Notifications → Phone connection to generate a five-minute QR.
   Scan it using the companion's own QR button. Camera frames never leave the app.
3. Grant notification access. If Android blocks a sideloaded app's special access,
   review its App info → menu → Allow restricted settings, then return to grant it.
4. Approve the actually paired PC as a self-managed companion when Android supports
   it; allow background connection and start the service. These are Android consent
   dialogs, not permissions that the PC can grant on the user's behalf.

The one-time `enable-lan.ps1` administrator action allows TCP 8766 for the office
Python executable, its selected private IPv4 address and LocalSubnet peers only.
It does not expose the localhost office API on port 8765. It refuses to overwrite
an existing rule with a different scope or executable.

The app buffers up to 150 notification revisions and acknowledgments privately.
Complete active inventories reconcile notification removals. Dismiss commands bind
both key and revision, so a newer notification is not canceled for an older card.
Notifications that Android marks non-clearable are reported unsupported.
Notification removal is different from reading a messenger conversation.

OTP and other sensitive notifications can be redacted by Android. A self-managed
association is requested only for the actually authenticated PC; no signature-only
computer role or sensitive-notification permission is claimed. Actual access needs
confirmation on the user's device. No bulk SMS database access is requested.

## Build

`build.py` uses JDK 17, Google's Android 35 platform and build-tools 35.0.0, plus
ZXing Core 3.5.3. These archives are downloaded to `.provider_runtime/android-build`
and checked against Google's repository metadata / Maven checksums. Run with the
office Python interpreter. The resulting APK, signing keystore, password, SDKs and
PC TLS material are private ignored files. Keep the signing key for future updates.
No tests or model inference are run by the build.

ZXing Core is licensed under Apache License 2.0; the APK includes its notice and
license in assets. Google's SDK components remain subject to their SDK licenses.
