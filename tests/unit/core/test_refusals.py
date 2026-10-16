# SPDX-License-Identifier: Apache-2.0
"""Why a touch on an element is refused before it lands, said so the agent knows what to do instead."""

from __future__ import annotations

import pytest

from sim_mirror.config.schema import OBSERVE_WORDS, find
from sim_mirror.core.refusals import ObserveGuard, matched_word, observe_words, touch_refusal
from sim_mirror.perception.snapshot import Element


def test_an_element_nothing_covers_may_be_touched() -> None:
    assert touch_refusal("e1", Element("e1", "button", "Save", None, 201, 400)) is None


def test_one_under_the_keyboard_is_refused_with_how_to_uncover_it() -> None:
    under = Element("e7", "field", "", None, 201.4, 625.6, covered="keyboard")
    assert touch_refusal("e7", under) == (
        "e7 is under the keyboard: a touch at (201,626) would land on the keyboard. First close the keyboard or "
        "scroll it into view and then use e7 again"
    )


# -- observe mode ----------------------------------------------------------------------------------------------------

WORDS = observe_words(OBSERVE_WORDS)


@pytest.mark.parametrize(
    ("label", "matched"),
    [
        ("Save", "save"),
        ("Save changes", "save"),
        ("Saved items", None),
        ("Unsaved", None),
        ("SIGN OUT", "sign out"),
        ("Logout", "logout"),
        ("Log-out", "log-out"),
        ("Delete Account…", "delete"),
        ("Remove from Home Screen", "remove"),
        ("Preset", None),
        ("Payment methods", None),
        ("Buy Pro", "buy"),
        ("Erase All Content and Settings", "erase"),
        ("", None),
    ],
)
def test_a_label_is_matched_by_whole_words_ignoring_case(label: str, matched: str | None) -> None:
    assert matched_word(label, WORDS) == matched


def test_a_persons_own_words_replace_the_english_ones_and_scripts_without_spaces_match_anywhere() -> None:
    own = observe_words(" Löschen ,ABMELDEN, 删除, , löschen ")
    assert own == ("löschen", "abmelden", "删除")
    assert matched_word("Konto löschen", own) == "löschen"
    assert matched_word("Gelöschte Objekte", own) is None
    assert matched_word("删除账户", own) == "删除"
    assert matched_word("Delete", own) is None
    assert matched_word("Delete", ()) is None


def test_observe_mode_is_a_guard_only_while_it_is_on() -> None:
    assert ObserveGuard.of(False, OBSERVE_WORDS, confirmed=False) is None
    guard = ObserveGuard.of(True, "send", confirmed=True)
    assert guard == ObserveGuard(("send",), confirmed=True)
    send, field = Element("e2", "button", "Send", None, 300, 40), Element("e1", "field", "", None, 200, 40)
    assert guard.matched([field]) is None
    assert guard.matched([field, send]) == (send, "send")
    assert guard.refusal("(300,40)", send, "send").startswith('(300,40) on "Send" looks like it changes something')
    assert guard.refusal('e2 "Send" (300,40)', send, "send").startswith('e2 "Send" (300,40) looks like')


def test_a_word_of_only_hyphens_matches_nothing_and_the_list_is_one_line() -> None:
    assert matched_word("Delete - Now", observe_words("-, --")) is None
    words = find("agent.observe_words")
    assert words is not None and words.errors("delete\nsave") == [
        "agent.observe_words must be words separated by commas, on one line of at most 1000 characters"
    ]
    assert words.rule.parse(" delete ") == "delete" and "separated by commas" in words.rule.describe()
