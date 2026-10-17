# Direct Android notification connection

**Goal:** Replace Phone Link window scraping with a phone companion that synchronizes notifications directly with AI OFFICE on the local network.

**User scope:** PC uses Ethernet; phone uses local Wi-Fi; outside-office access is unnecessary. Receive/read/confirm only; no replies. Retain cards and Aemeath's existing notification tools. No tests or paid model calls unless requested.

**Architecture:** A separate TLS-only LAN receiver exposes pairing and authenticated notification sync, never the office/work/files APIs. One-use five-minute QR secret pins the PC certificate; successful pairing issues a per-device token stored privately. Device revocation invalidates it. Android NotificationListenerService sends keyed revisions, a complete active inventory, and dismiss acknowledgments. Office confirmation queues an exact-revision dismiss; actual phone response determines sync completion. Removed phone notifications reconcile office confirmation without claiming messenger conversation read status. Non-dismissible alerts stay explicitly unsynchronized.

Android app uses a foreground connected-device service and persistent pending removals. It filters summary/own-service notifications and excludes its own status alert. Android's user-approved, self-managed CompanionDeviceManager association represents the actually paired PC; no protected computer role or signature permissions are assumed. Sensitive/OTP access remains device-policy dependent and requires actual phone confirmation.

- [x] Implement private TLS pairing/receiver and authenticated, bounded sync.
- [x] Connect keyed deduplication, inventory removal and dismiss commands to existing cards/tools. Stop Phone Link capture/start/recovery when direct mode is activated.
- [x] Build Android listener, QR pairing, persistent connection, user-approved companion association, and source control UI. Compile a signed installable APK; keep SDKs, keys and APKs private/ignored.
- [x] Add office pairing/revoke UI and local APK download, preserving localhost-only app APIs.
- [x] Syntax/build/read-only operational checks only. Back up DB/settings before idle server activation; real phone installation/permissions and receipt validation remain user steps.

**Completion boundary:** APK and receiver are usable for real pairing; never claim a phone is connected, OTPs readable, or dismissal synchronized without its actual receipt. No existing notifications/history are deleted and no Windows app is uninstalled.

## Evidence

Android NotificationListenerService provides posted/removed callbacks, active inventory, and cancelNotification. Android 15 restricts untrusted OTP notification access; trusted companion associations are an exception. Newer sensitive-notification and SMS privileges are not assumed. Tasker/AutoNotification and KDE Connect were checked as alternatives; a dedicated client directly fits the existing per-card synchronization requirement without another PC window reader.


## Activation evidence

Signed APK compiled with JDK 17 / Google API 35 tools and verified APK v2/v3. The final APK remains private. Server PID 10432 initially activated direct mode, status waiting with zero paired devices. Narrow firewall administrator approval completed and rule scope was read back. Local APK endpoint matches the built file; TLS peer certificate matches the private PC pin. Settings bytes and task rows match the pre-activation backup; notification history count did not decrease. No synthetic notification, pairing/sync command, test suite or model inference was performed. Actual phone installation and confirmation are pending user action.

Ruling: old Phone Link capture code remains as inactive reference, but active start/confirmation/reconnect paths use direct connection and do not launch it. Historical Phone Link cards are confirmed locally. Computer-profile/signature permissions and bulk SMS access are not requested; sensitive content remains subject to Android policy.


Final activation PID 13420 includes receipt timestamp separation and removes the legacy confirmation fallback. Live local management API generated a real one-use 300-second QR (not a handset pairing). Paired devices remain zero. All pre-task notification IDs/source/title/body/received values, tasks and settings were compared and preserved. No private QR, key, token or notification content is in the journal or tracked sources.
