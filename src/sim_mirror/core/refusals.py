# SPDX-License-Identifier: Apache-2.0
"""Why a touch that would begin on an element is refused before it lands.

A step names an element by its ref and lands at its point. Some elements are listed but cannot be touched there: one
under a tab bar, a toolbar or the keyboard (`perception.occlusion`), where the touch would land on what covers it -- a
tap meant for a row would switch tabs, and the step would still say ok. `touch_refusal` says why such a touch is
refused, in words that tell the agent what to do instead; the step is not played.

**Observe mode** (``agent.observe``) is for walking through an app on an account that is not the agent's to change --
a shared test account, a person's own phone. What reads like it deletes, saves, sends, buys or signs out
(``agent.observe_words``, and the words of ``agent.observe_languages``) is refused unless the call says
``confirm: true``. It is a guard rail against a slip, not a lock: an agent can confirm, and is told it may only when it
was asked to. Refused are:

- a tap, a long press or typing into such an element -- or a switch, which changes a setting whatever it says
  (`ObserveGuard.matched`);
- a sideways swipe -- or a drag -- that starts on one, on an element whose custom actions have such a word, or on a
  list row, whose swipe actions can delete it: a cell or a button at least `ROW_WIDTH_MIN` across and `ROW_ASPECT`
  times as wide as it is tall (`ObserveGuard.swiped`). A swipe up or down scrolls, and one from the left edge goes
  back: neither is refused;
- a drag that ends on such an element: a drop onto a trash can;
- ``submit``, or pressing return, on a screen with a button, a link or a key such a word names -- a form's Send, the
  keyboard's own send key -- since return may press it; never in a search field (`ObserveGuard.submitted`).

What is under the touch is read from the agent's last look at the screen: the element a ref names, and every element
whose frame holds the point -- so a tap by point on a "Delete" button is refused as a tap on its ref is. A touch by
point is also asked of the device, where it can say what is at a point (`perception.in_front`): a snapshot lists no
element that says nothing, and one capped at ``max_elements`` lists only the first, while the device's hit test answers
whatever is there -- an unlabelled icon identified as ``trash``. An element is read by its label and by its
identifier, whose words a developer runs together -- ``deleteButton``, ``trash.fill``, ``remove_row`` -- and the
keyboard's own delete key, which deletes a character, never. Words are matched whole, ignoring case: ``save`` refuses
"Save" and "Save changes", not "Saved items"; a space in a word also matches a hyphen or no space, so ``log out``
refuses "Logout" and "Log-out". Scripts written without spaces -- Chinese, Japanese, Thai -- are matched anywhere in a
label.

A button its app styles **destructive** is refused whatever it says -- "Yes", "Continue", a word in a language no list
has: an alert's or an action sheet's ``UIAlertAction`` styled ``.destructive``, SwiftUI's ``Button(role:
.destructive)`` in one, and one drawn in place when the app reads SwiftUI's debug data. Only an app that links the app
SDK says so (`perception.model.DESTRUCTIVE`): accessibility reads such a button as any other, so without the SDK it is
refused by what it says, as everything else is.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from sim_mirror.config.observe_languages import words_in
from sim_mirror.perception.occlusion import KEYBOARD
from sim_mirror.perception.snapshot import Element

#: Scripts written without spaces between words, matched anywhere in a label: a word boundary never falls inside them.
_UNSPACED = ("CJK", "HIRAGANA", "KATAKANA", "THAI", "LAO", "KHMER", "MYANMAR", "TIBETAN")
#: Where the words of a name a developer gave run together: a dot, an underscore, a slash, a colon -- and a capital
#: after a small letter or a digit, or before one in a run of capitals (``URLDelete``).
_JOINS = re.compile(r"[._:/]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
#: How a list row is listed: as a cell, or -- as idb_companion and Xcode list one -- as the button that spans it.
ROW_KINDS = frozenset({"cell", "button", "link"})
#: The narrowest a row is, in points, and how many times its height it is across at least: a row of a list on any
#: iPhone is, and a button, a card or a chip in a carousel is not.
ROW_WIDTH_MIN = 240.0
ROW_ASPECT = 3.0
#: A swipe that starts this close to the left edge and goes right is iOS's own: back.
EDGE_PT = 20.0
#: What a sideways swipe on a row is matched as, said where a word would be.
ROW = "a sideways swipe on a row"
#: What a button its app styles destructive is matched as, said where a word would be.
DESTRUCTIVE = "destructive"
#: What a switch is matched as, said where a word would be: a tap flips it, whatever it says.
SWITCH = "switch"
#: The kinds a switch is listed as. Android's check boxes, radio buttons and chips read as switches as well
#: (`perception.snapshot.KINDS`), and iOS 26's accessibility lists a switch as a check box -- as idb_companion reads
#: Settings' (measured on an iPhone 17 Pro simulator, iOS 26.5, 2026-10-31) -- so check boxes and radio buttons are too.
SWITCH_KINDS = frozenset({"switch", "toggle", "checkbox", "radiobutton"})
#: What return may press: the kinds a form's button and a key of the keyboard are listed as.
PRESSED_KINDS = frozenset({"button", "link", "key"})
#: The keyboard's own delete key, as readers name it: it deletes a character, not an account.
DELETE_KEY = "delete"
_CONFIRM = "If the person you work for asked for exactly this, call again with confirm: true"


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
    return tuple(dict.fromkeys(word for word in (_folded(" ".join(part.split())) for part in text.split(",")) if word))


def _folded(text: str) -> str:
    """Text as it is matched: composed one way -- an é is one character, not an e and its accent -- ignoring case."""
    return unicodedata.normalize("NFC", text).casefold()


def spelled(name: str) -> str:
    """A name a developer gave as the words it runs together: ``deleteAccountButton`` is "delete Account Button" and
    ``xmark.bin`` is "xmark bin"."""
    return _JOINS.sub(" ", _CAMEL.sub(" ", name))


def _spaced(char: str) -> bool:
    """Whether a word ends at this character only where a space or a mark does: true of letters in most scripts."""
    return (char.isalnum() or char == "_") and not unicodedata.name(char, "").startswith(_UNSPACED)


@functools.lru_cache(maxsize=32)
def _pattern(words: tuple[str, ...]) -> re.Pattern[str] | None:
    """One expression matching any of the words, each whole where its script separates words."""
    parts = []
    for word in words:
        body = r"[\s\-‐‑]*".join(re.escape(piece) for piece in re.split(r"[\s\-]+", word) if piece)
        if not body:
            continue
        before = r"(?<!\w)" if _spaced(word[0]) else ""
        after = r"(?!\w)" if _spaced(word[-1]) else ""
        parts.append(f"{before}{body}{after}")
    return re.compile("|".join(parts), re.IGNORECASE) if parts else None


def matched_word(label: str, words: tuple[str, ...]) -> str | None:
    """The part of a label one of the words matches, or None."""
    pattern = _pattern(words)
    found = pattern.search(_folded(label)) if pattern is not None and label else None
    return found.group(0) if found else None


def _names(element: Element) -> Iterator[str]:
    """What observe mode reads an element by: its label, whose dots part words as a symbol's name's do, and its
    identifier as words. A key of the keyboard is read by its label alone, and its delete key not at all."""
    if element.key:
        if DELETE_KEY not in (element.identifier.casefold(), element.label.casefold()):
            yield element.label
        return
    yield _JOINS.sub(" ", element.label)
    yield spelled(element.identifier)


def _row(element: Element) -> bool:
    """Whether an element is a row of a list, which a sideways swipe can delete."""
    frame = element.frame
    return (
        element.kind in ROW_KINDS
        and frame is not None
        and frame.width >= ROW_WIDTH_MIN
        and frame.width >= ROW_ASPECT * frame.height
    )


def _called(element: Element) -> str:
    """How a refusal names an element: by its label, or else the identifier that matched."""
    return f'"{element.label or element.identifier}"'


def _on(named: str, element: Element) -> str:
    """What a step's line adds to say which element it lands on, when it does not say so already."""
    return "" if element.label and f'"{element.label}"' in named else f" on {_called(element)}"


def _changes(word: str) -> str:
    """Why what was matched looks like it changes something: what it says, or how its app styles it."""
    if word == DESTRUCTIVE:
        return "is a button its app styles destructive"
    if word == SWITCH:
        return "is a switch, which changes a setting"
    return f'looks like it changes something ("{word}")'


@dataclass(frozen=True)
class ObserveGuard:
    """What observe mode refuses in one call: what acts on elements one of `words` names, unless it is `confirmed`."""

    words: tuple[str, ...]
    #: The call said ``confirm: true``: what would be refused is played, and its line says it was confirmed.
    confirmed: bool = False

    @classmethod
    def of(cls, observe: bool, words: str, *, confirmed: bool, languages: str = "") -> ObserveGuard | None:
        """The guard a scope's settings give a call: None while observe mode is off. The words of each of
        `languages` (`config.observe_languages`) are kept off as `words` are."""
        return cls(observe_words(f"{words}, {words_in(languages)}"), confirmed) if observe else None

    def matched(self, under: Sequence[Element]) -> tuple[Element, str] | None:
        """The first element under the touch whose label or identifier one of the words names, and what it matched --
        or that its app styles destructive, said as `DESTRUCTIVE`, or that it is a switch, said as `SWITCH`."""
        for element in under:
            for name in _names(element):
                word = matched_word(name, self.words)
                if word is not None:
                    return element, word
            if element.destructive:
                return element, DESTRUCTIVE
            if element.kind in SWITCH_KINDS:
                return element, SWITCH
        return None

    def swiped(
        self, under: Sequence[Element], start: tuple[float, float], end: tuple[float, float]
    ) -> tuple[Element, str] | None:
        """What a swipe from `start` to `end`, beginning on `under`, may act on: when it goes sideways, an element one
        of the words names -- by its label, its identifier or one of its custom actions -- or else a list row, said as
        `ROW`. A swipe up or down scrolls, and one from the left edge goes back: neither acts on anything."""
        across, down = end[0] - start[0], end[1] - start[1]
        if abs(across) <= abs(down) or (start[0] <= EDGE_PT and across > 0):
            return None
        hit = self.matched(under)
        if hit is not None:
            return hit
        for element in under:
            for action in element.actions:
                word = matched_word(action, self.words)
                if word is not None:
                    return element, word
        row = next((element for element in under if _row(element)), None)
        return (row, ROW) if row is not None else None

    def submitted(self, screen: Sequence[Element], into: Element | None) -> tuple[Element, str] | None:
        """What return may press on a screen, typed into `into` or else the field being edited: a button, a link or a
        key there one of the words names. Return in a search field searches, and presses nothing else."""
        field = into if into is not None else next((element for element in screen if "editing" in element.flags), None)
        if field is not None and field.kind == "search":
            return None
        return self.matched([element for element in screen if element.ref and element.kind in PRESSED_KINDS])

    def refusal(self, named: str, element: Element, word: str, *, done: str = "touched") -> str:
        """Why a step on `named`, which would land on `element`, is refused: it was not `done`."""
        return (
            f"{named}{_on(named, element)} {_changes(word)}, and agents only observe here (agent.observe): it was not "
            f"{done}. {_CONFIRM}"
        )

    def swipe_refusal(self, named: str, element: Element, word: str) -> str:
        """Why a sideways swipe from `named`, which begins on `element`, is refused."""
        said = {
            ROW: "a row, whose swipe actions can delete it",
            DESTRUCTIVE: "a button its app styles destructive",
            SWITCH: "a switch, which changes a setting",
        }
        what = said.get(word, f'it ("{word}")')
        return (
            f"a sideways swipe from {named}{_on(named, element)} can act on {what}, and agents only observe here "
            f"(agent.observe): it was not swiped. To scroll, swipe up or down. {_CONFIRM}"
        )

    def drop_refusal(self, named: str, element: Element, word: str) -> str:
        """Why a drag `named` -- ``a drag to (x,y)`` -- which would end on `element`, is refused."""
        return (
            f"{named} ends on {_called(element)}, which {_changes(word)}, and agents only observe here "
            f"(agent.observe): it was not dragged. {_CONFIRM}"
        )

    def submit_refusal(self, named: str, element: Element, word: str) -> str:
        """Why `named` -- ``submit`` or ``press return`` -- is refused while `element` is on the screen."""
        return (
            f"{named} presses return, which may press {element.ref} {_called(element)} on this screen, and that "
            f"{_changes(word)}; agents only observe here (agent.observe): it was not played. {_CONFIRM}"
        )
