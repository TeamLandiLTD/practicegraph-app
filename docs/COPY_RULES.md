# Copy rules — how this app explains a number

The app's wording should be concise and down to earth: explain what a
metric means instead of just posting it.

The honesty rules (observations not judgments, nothing about the person,
withheld-is-explained, every association with its source) stay exactly as
they are. What changes is WHERE the words sit and HOW MANY there are. The
card face explains; the ⓘ proves.

## The five rules

1. **Lead with what it means, in one plain sentence.** The number is the
   evidence for the sentence, never the headline.
   - not: "1,280 failed runs ▲101 vs your prior 28 days"
   - but: "More of your runs failed this month than last."
2. **Compare to something the reader already owns.** Their own last month,
   their own normal day. Words, not glyphs.
   - not: "▲ 61 vs your prior 28 days"
   - but: "up from 115 last month"
3. **The reader's words, not the study's.** Every metric has a plain name
   (the word list below). The technical name can live in the ⓘ.
4. **One idea per card, three lines on the face**: what it says → one
   supporting fact → what to do, if anything. Method, source, caveat and
   the withheld-explanation go behind the ⓘ or into one quiet footer line.
   They are still there; they stop crowding.
5. **Round the numbers.** "about 1,300" not "1,280"; one number per
   sentence; dollars to the dollar above a hundred.

## The card face, anatomically

```
TITLE (plain name)                                  quiet meta (window)
Headline sentence — what it means, with the one number that proves it.
One supporting fact, if the headline needs it.
[what to do — only when there is something to do]
ⓘ  how it is counted · what it leans on · what is left out
```

Hints (the ⓘ) carry the method, the source and the caveat. They are capped
at 220 characters. Headlines at 140. Titles at 32. A test enforces the caps
so the voice cannot drift back.

## The word list

| Was | Is |
|---|---|
| Verification load | Checking the agent's work |
| Iterations to a passing run | Tries before it worked |
| Failed runs | Runs that failed |
| Retries after failure | Quick retries |
| Pauses | Pauses inside a session |
| Switches | Jumps between tasks |
| Handoff size | How much you hand over at once |
| At the judgment moment | When it asks you to decide |
| Shaping the output | What you change afterwards |
| Capability | Your longest focused stretch |
| Load balance | This week against your usual |
| Features in use | What you actually use |
| Connectors and plugins | What each tool is connected to |
| Skills installed | Skills you have |
| Project folders | Where the work happened |
| Your models | Which model does what |
| Work units / What a piece of work runs | What one piece of work costs |
| Capability per window | What the month cost you |
| Where the day ends | When your day ends |
| Calibration | How long did that feel? |
| The break that restores | (removed) |
| association · not a measurement | (moves into the ⓘ) |
| guidance · not a measurement | (moves into the ⓘ) |
| vs your prior 28 days | than last month / since last month |

## What never changes

Section titles use sentence case at 20px. Supporting copy uses 14–15px;
small labels and tags use at least 13px. Names and tags use the normal interface
font, with monospace reserved for figures and code. Remove repeated labels and
technical provenance before shrinking text to fit. Keep dates, meaningful states
and source links readable; let long names and tags wrap in narrow windows.

- No sentence about the person's state: not "stressed", "fatigued",
  "over-reliant", "deskilling". The count, the share, the comparison.
- Nothing invented; a withheld reading says why, in the ⓘ.
- Every association keeps its source — in the ⓘ.
- The lexicon gate (FR-FOC-8) scans every rewritten string.

## What the first pass exposed (2026-08-23)

Rewriting a line to say what it means is a test of whether it means
anything. Three readings failed that test on a real month of logs and were
fixed in the engine, each with a test:

- **"You changed 0% of what came back."** The rework channel had fired (a
  handful of edits among many thousands of results), so the facet cleared its gate - and a rounded
  zero read as "nothing", which is false. Under one percent, the line now
  carries the count (`shaping-line-few`).
- **A part of a session is not a session.** Claude Code writes subagent
  transcripts under `<session>/subagents/`; a sizeable share of a month's
  "sessions" were those. They still count as feature use and never as a
  session, a work-mix bucket, or a project visit.
- **"Most of your sessions were not code work."** Conversation-only and
  read-only sessions are what code work looks like from the logs. The nudge
  toward the productivity profile now rests on the one bucket that tells
  the audiences apart - document work - at 40% of the sessions that did
  something, with at least 20 of them.

The rule that follows: before a card's line is called done, read it on the
scratch store against your own month. If the sentence would make you argue
with it, the arithmetic is wrong, not the wording.

## What the second pass exposed (2026-09-10)

The pages still carried a lot of information nobody needed. Every page added
since August (the usage explorer, Improve setup,
Learning paths, Practice hours, Reading) had grown the old voice back, in six
repeating shapes rather than eighteen separate problems:

- **A provenance line on every curated card** (`Curated by TeamLandi ·
  edition dated … · Catalog source`, eleven times). Now one short chip:
  `News edition 2026-09-08`, the curator in its title, the source as the
  link, the stale warning on the same line only when the edition is old.
- **The billing disclaimer on three faces.** Once, behind the ⓘ beside the
  money label. The comparison the number needed ("up from $120 the week
  before") replaced the paragraph that hedged it.
- **A privacy reassurance per page.** The header badge carries the privacy
  fact; the per-page sentences went behind the ⓘ.
- **Method and caveat sentences on the face** ("does not establish", "not a
  measurement of", "may be missing" - about forty). Behind the ⓘ, or into
  the fold that already existed.
- **Captions that narrated the controls** ("Select a bar to inspect…",
  "Sorted by listed-rate estimate…", "That is the overview."). Cut.
- **The same fact twice** (a green summary above a card that says it
  again; two moves citing one finding). Said once.

Engine lines follow the same rules: `skill_ledger` and `advisor` speak in
words ("went from 230K to 180K", never "232.1K → 194.5K") and carry their
caveat as a separate `note` for the ⓘ. `tests/test_copy_rules.py` now pins
the cut narration so it cannot drift back into a rendered component or a CLI
reply.
