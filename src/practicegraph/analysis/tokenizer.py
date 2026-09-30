"""Tokenizer generations — why a token count is not always a token count.

Anthropic changed tokenizer with Claude 4.7: the newer models encode the same
text into materially more tokens (Anthropic's own docs put it at roughly 30%).
Sonnet 4.6 and earlier use the previous one.

That matters here more than anywhere else in the product, because several
readings compare token volumes across time or across cohorts. If the compared
groups sit on opposite sides of that change, part of the difference is an
artifact of encoding rather than anything the person did — and a mirror that
reports an artifact as behaviour is worse than one that stays quiet.

The rule this module supports: **when a comparison straddles the change, say
so.** We do not attempt to rescale. A correction factor would be a made-up
number applied to real data, and the honest move is a caveat, not a fudge.

Money is deliberately NOT caveated. More tokens at the same rate is more spend;
the person paid it. Only *token-count* comparisons are affected.
"""

from __future__ import annotations

from collections.abc import Iterable

# Model prefixes that use the wider tokenizer (Claude 4.7 and later, plus the
# Mythos line). Matched as prefixes so dated builds are covered.
WIDE_TOKENIZER_PREFIXES: tuple[str, ...] = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-fable-",
    "claude-mythos-",
    "claude-sonnet-5",
)


def uses_wide_tokenizer(model: str) -> bool:
    """Whether a model id encodes with the post-4.7 tokenizer."""
    return any(model.startswith(prefix) for prefix in WIDE_TOKENIZER_PREFIXES)


def straddles_tokenizer_change(models: Iterable[str]) -> bool:
    """True when a set of model ids spans BOTH tokenizer generations, which is
    exactly when a token-count comparison over them carries an artifact.

    Only Anthropic ids are classified. OpenAI models are unaffected by this
    change, so a window that is entirely Codex never straddles.
    """
    wide = narrow = False
    for model in models:
        name = str(model)
        if not name.startswith("claude-"):
            continue
        if uses_wide_tokenizer(name):
            wide = True
        else:
            narrow = True
        if wide and narrow:
            return True
    return False
