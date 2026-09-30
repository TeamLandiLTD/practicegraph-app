## What changed

<!-- One paragraph. What a user of the app or a reader of the code will notice. -->

## Why

<!-- The user-visible problem or the evidence behind a measurement change. Link the issue. -->

## How it was checked

- [ ] `python -m ruff check src tests`, `python -m mypy`, `python -m pytest`
- [ ] `npm test` and `npm run build` in `ui/` (rebuilt `webui/` committed with the change, if the UI changed)
- [ ] Golden diffs reviewed like an API change, if reports changed
- [ ] Synthetic fixtures only; no real logs, prompts, tokens, paths or databases

## Screenshots

<!-- Before and after for any navigation or measurement change. -->
