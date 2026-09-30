# Private practice history

PracticeGraph can keep a lifetime record of time the person chooses to count
toward an AI-work skill. Enable it in **Practice → Your practice history**.
Supported starting skills are AI-assisted development, research and analysis,
writing with AI, task framing, and reviewing/verifying results.

The total describes recorded investment. It is not a proficiency score, a
percentage of mastery, or a prediction of when someone will become an expert.
The next small milestone is shown alongside the total. A personal hours goal,
including 10,000 hours, is optional and carries no expertise guarantee.

## Recording and reviewing

- Start a session for the chosen skill. The server saves elapsed intervals;
  refreshing or changing dashboard sections does not reset the record.
- Pause for breaks, resume explicitly, and finish when done. The existing
  Quick break and Long rest controls also pause the practice session.
- Review completed sessions before adding them to the confirmed total. Choose
  a shorter duration, change the skill, add an optional closed reflection, or
  skip the session. A correction cannot inflate captured time.
- Use **Add a missed practice session** for completed practice away from the
  timer. Start/end inputs use the browser device's local timezone. Entries are
  limited to 12 hours and cannot overlap existing unfinished or confirmed time.
- Recent activity estimates use intervals between positively classified human
  interactions at most five minutes apart. Gaps, agent-only traffic, recorded
  breaks, and already recorded intervals are excluded. Parallel activity counts
  once. Estimates are grouped by local day over the last seven days and never
  enter the total until explicitly confirmed for a skill. These sparse log
  intervals are not a measurement of learning or all working time.

## Clock and integrity rules

The app sends an authenticated heartbeat every 30 seconds. If more than 90
seconds pass without contact, or the clock moves backwards, the session pauses
at the last acknowledged heartbeat. A gap up to 90 seconds is treated as
continuous recording; longer sleep, network outages, and closed-app time are
not backfilled. Reopen the app and explicitly resume; a manually entered
session can recover time actually practised while offline. Review sessions
to remove idle time before confirming them.

There is one running or paused session across all app windows. Concurrent
heartbeats serialize in SQLite and do not double-count. A timer session stops
at 12 recorded hours for review. Unconfirmed time stays separate from totals.
Weekly totals begin at Monday midnight in the saved schedule timezone; lifetime
totals retain all confirmed records. The UI displays the latest 100 confirmed
sessions, while exports retain the full journal.

## Storage, backup, and privacy

SQLite schema 14 adds dedicated `practice_time_entries` and
`practice_time_settings` tables. These are independent of the activity ingestion
tables and the coach's shorter feedback history. Pausing tracking preserves the
record. Clearing coach feedback does not clear practice time, and clearing the
practice-time journal requires a separate explicit confirmation.

Export the JSON backup before uninstalling, deleting the data directory, or
moving devices. Keeping the data directory preserves the journal across normal
upgrades. Restore validates all entries in one transaction, merges exact
duplicates once, and rejects conflicting IDs and overlapping time. Unfinished
timer sessions become reviewable drafts; restore never resumes a timer. Current imports
accept up to 50,000 records and a 16 MB JSON file. Preserve a separate copy of
the exported backup.

The record contains skill IDs, time intervals, source labels, and optional
closed reflection choices. It contains no prompts or file/project paths and
never enters shared aggregates. `/api/practice-time`, its export, and its
actions use the existing localhost token and Host protections. Restoring a
local file sends it only to the local engine, not to the public content site.
