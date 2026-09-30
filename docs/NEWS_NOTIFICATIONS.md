# Editorial news attention

Selected news can reach the tray with an importance label set in the edition.
This is public editorial copy, separate from the private-observation
notification vocabulary.

News with no `attention` metadata is ordinary: available in Reading and the
Latest news window, with a local unread count. An optional `attention` object has
exactly `urgency` (`important` or `urgent`), `reason` (up to 280 characters),
`starts_at`, and `expires_at` (UTC `YYYY-MM-DDTHH:MM:SSZ`). Its interval must be
positive and at most 72 hours. All copy passes the existing news scans. Expiry
ends the importance label and notification eligibility; it does not delete news.

```json
"attention": {
  "urgency": "important",
  "reason": "The supported endpoint changes tomorrow; review the migration steps.",
  "starts_at": "2026-09-11T09:00:00Z",
  "expires_at": "2026-09-12T09:00:00Z"
}
```

The example is illustrative. Story IDs persist across editions and copy edits;
those edits do not trigger new alerts. A genuine escalation from important to
urgent is eligible once more, within caps.

## Delivery and controls

Windows tray menu: **Latest news (N unread)** opens a separate 420-by-560 logical
pixel window near the lower right of its monitor. Notification clicks open that
same window. The window shows source links, summaries, editorial reasons, Mark
read, Copy link, and notification settings. Opening a window or previewing an
article is not a read acknowledgment. Explicit source clicks/Mark read and
opening a current story's disclosure in Reading acknowledge the story locally.

Default: important and urgent OS notifications, with a compact window also
requested for urgent stories. Automatic windows do not take keyboard focus;
manual tray/notification clicks can focus an existing news window. Users can
select urgent-only or off, disable automatic windows, and snooze for one or
24 hours. Settings are also available under Reading's **News notifications**.
Quiet hours apply to every level, using the confirmed schedule timezone or
local time/default quiet hours when no schedule is confirmed.

Delivery is at most three stories per rolling 24 hours, at least one hour apart.
Delivery attempts are reserved atomically and unavailable delivery retries no
more often than every 15 minutes. Quiet hours and snoozes do not consume delivery.
Seen and delivered state survives restarts; same-ID copy changes do not reset it.
Expired and not-yet-active stories never interrupt. The local cached catalog is
read without fetching article links. Freshness follows the existing catalog and
agent polling cadence; this is not instant push infrastructure.

`GET/POST /api/news` uses the same token/host gates as the dashboard. News state
lives in the local SQLite meta store and is excluded from aggregate sharing.
The tray status artifact contains only `news_unread`, never headlines or URLs;
its count refreshes with the next tick. News delivery is a separate fail-open
agent category. A shell delivery return means the OS accepted the toast or a
window process was launched, not that the user saw it.

## Compatibility and verification

Legacy editions remain readable and do not suddenly create notifications.
Older clients reject the additional field: ship a compatible reader before
publishing editions with attention metadata. This change does not publish an
edition or install an updated desktop binary. The Windows shell implements the
native popup/toast door. Other platforms can display labels and settings, but
native news notification delivery remains unavailable until their shell adopts it.

Tests cover validation/normalization, local API authentication and closed actions,
read state, expiry, escalation/deduplication, failed delivery retries, quiet hours,
timezone use, caps, and UI controls. Native build checks do not establish actual
Windows notification permission, protocol registration, monitor placement, or
Focus Assist behavior; verify these in the packaged desktop smoke test.
