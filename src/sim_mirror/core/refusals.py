# SPDX-License-Identifier: Apache-2.0
"""Why a touch that would begin on an element is refused before it lands.

A step names an element by its ref and lands at its point. Some elements are listed but cannot be touched there: one
under a tab bar, a toolbar or the keyboard (`perception.occlusion`), where the touch would land on what covers it -- a
tap meant for a row would switch tabs, and the step would still say ok. `touch_refusal` says why such a touch is
refused, in words that tell the agent what to do instead; the step is not played.

**Observe mode** (``agent.observe``) is for walking through an app on an account that is not the agent's to change --
a shared test account, a person's own phone. A tap, a long press or typing into an element whose label reads like it
deletes, saves, sends, buys or signs out (``agent.observe_words``) is refused, unless the call says ``confirm: true``.
It is a guard rail against a slip, not a lock: an agent can confirm, and is told it may only when it was asked to.

What is under the touch is read from the agent's last look at the screen: the element a ref names, and every element
whose frame holds the point -- so a tap by point on a "Delete" button is refused as a tap on its ref is. A label is
matched by whole words, ignoring case: ``save`` refuses "Save" and "Save changes", not "Saved items"; a space in a word
also matches a hyphen or no space, so ``log out`` refuses "Logout" and "Log-out". Scripts written without spaces --
Chinese, Japanese, Thai -- are matched anywhere in a label. The words are English unless a person sets their own:
an app in another language is guarded only by words in that language.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from sim_mirror.perception.occlusion import KEYBOARD
from sim_mirror.perception.snapshot import Element

#: Scripts written without spaces between words, matched anywhere in a label: a word boundary never falls inside them.
_UNSPACED = ("CJK", "HIRAGANA", "KATAKANA", "THAI", "LAO", "KHMER", "MYANMAR", "TIBETAN")


def touch_refusal(ref: str, element: Element) -> str | None:
    """Why a touch beginning on this element is refused, or None when it may land."""
    if element.covered is None:
        return None
    named = f'{ref} "{element.label}"' if element.label else ref
    where = f"({round(element.x)},{round(element.y)})"
    if element.covered == KEYBOARD:
        instead = "close the keyboard or scroll it into view"
    else:
        instead = "scroll it into view with a swipe that starts on the content, not the bar,"
    return (
        f"{named} is under the {element.covered}: a touch at {where} would land on the {element.covered}. "
        f"First {instead} and then use {ref} again"
    )


def observe_words(text: str) -> tuple[str, ...]:
    """The words a list of them names, separated by commas: each as one line, its spaces single, ignoring case."""
    return tuple(
        dict.fromkeys(word for word in (" ".join(part.split()).casefold() for part in text.split(",")) if word)
    )


def _spaced(char: str) -> bool:
    """Whether a word ends at this character only where a space or a mark does: true of letters in most scripts."""
    return (char.isalnum() or char == "_") and not unicodedata.name(char, "").startswith(_UNSPACED)


@functools.lru_cache(maxsize=32)
def _pattern(words: tuple[str, ...]) -> re.Pattern[str] | None:
    """One expression matching any of the words, each whole where its script separates words."""
    parts = []
    for word in words:
        body = r"[\s\-\u2010\u2011]*".join(re.escape(piece) for piece in re.split(r"[\s\-]+", word) if piece)
        if not body:
            continue
        before = r"(?<!\w)" if _spaced(word[0]) else ""
        after = r"(?!\w)" if _spaced(word[-1]) else ""
        parts.append(f"{before}{body}{after}")
    return re.compile("|".join(parts), re.IGNORECASE) if parts else None


def matched_word(label: str, words: tuple[str, ...]) -> str | None:
    """The part of a label one of the words matches, or None."""
    pattern = _pattern(words)
    found = pattern.search(label.casefold()) if pattern is not None and label else None
    return found.group(0) if found else None


@dataclass(frozen=True)
class ObserveGuard:
    """What observe mode refuses in one call: touches on elements one of `words` names, unless it is `confirmed`."""

    words: tuple[str, ...]
    #: The call said ``confirm: true``: what would be refused is played, and its line says it was confirmed.
    confirmed: bool = False

    @classmethod
    def of(cls, observe: bool, words: str, *, confirmed: bool) -> ObserveGuard | None:
        """The guard a scope's settings give a call: None while observe mode is off."""
        return cls(observe_words(words), confirmed) if observe else None

    def matched(self, under: Sequence[Element]) -> tuple[Element, str] | None:
        """The first element under the touch whose label one of the words names, and what it matched."""
        for element in under:
            word = matched_word(element.label, self.words)
            if word is not None:
                return element, word
        return None

    def refusal(self, named: str, element: Element, word: str) -> str:
        """Why a step on `named`, which would land on `element`, is refused."""
        on = "" if element.label and f'"{element.label}"' in named else f' on "{element.label}"'
        return (
            f'{named}{on} looks like it changes something ("{word}"), and agents only observe here (agent.observe): '
            "it was not touched. If the person you work for asked for exactly this, call again with confirm: true"
        )
