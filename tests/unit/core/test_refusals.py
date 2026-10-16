# SPDX-License-Identifier: Apache-2.0
"""Why a touch on an element is refused before it lands, said so the agent knows what to do instead."""

from __future__ import annotations

import pytest

from sim_mirror.config import observe_languages
from sim_mirror.config.observe_languages import LANGUAGES, base, words_in
from sim_mirror.config.schema import OBSERVE_WORDS, find
from sim_mirror.core.refusals import (
    DESTRUCTIVE,
    ROW,
    ObserveGuard,
    matched_word,
    observe_words,
    spelled,
    touch_refusal,
)
from sim_mirror.perception.model import Frame
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


GUARD = ObserveGuard.of(True, OBSERVE_WORDS, confirmed=False, languages=LANGUAGES)
assert GUARD is not None


def button(label: str, identifier: str = "", **more: object) -> Element:
    return Element("e1", "button", label, None, 100, 100, identifier=identifier, **more)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "words"),
    [
        ("deleteAccountButton", "delete Account Button"),
        ("xmark.bin", "xmark bin"),
        ("remove_row", "remove row"),
        ("URLDelete", "URL Delete"),
        ("cell2Save", "cell2 Save"),
        ("com.apple.settings.general", "com apple settings general"),
    ],
)
def test_a_name_a_developer_gave_is_read_as_the_words_it_runs_together(name: str, words: str) -> None:
    assert spelled(name) == words


@pytest.mark.parametrize(
    ("element", "matched"),
    [
        # An icon-only button: iOS labels it with its symbol's name, or identifies it by it.
        (button("trash"), "trash"),
        (button("Trash", "trash"), "trash"),
        (button("xmark.bin"), "xmark bin"),
        (button("minus.circle.fill"), "minus circle"),
        (button("", "trash.fill"), "trash"),
        # A label that says nothing, and the identifier its developer gave it.
        (button("Done", "deleteAccountButton"), "delete"),
        (button("", "logOutButton"), "log out"),
        (button("", "btn_unsubscribe"), "unsubscribe"),
        (button("", "savedItemsCell"), None),
        (button("Pay", "applePay"), "pay"),
        (button("Settings", "com.apple.settings.general"), None),
        # The keyboard's delete key deletes a character; its send key sends.
        (button("delete", "delete", key=True), None),
        (button("Löschen", "delete", key=True), None),
        (button("send", "Send", key=True), "send"),
        (button("return", "Return", key=True), None),
    ],
)
def test_an_element_is_read_by_its_label_and_its_identifier(element: Element, matched: str | None) -> None:
    hit = GUARD.matched([element])
    assert (hit[1] if hit else None) == matched


@pytest.mark.parametrize(
    ("label", "matched"),
    [
        ("Konto löschen", "löschen"),
        ("Supprimer le compte", "supprimer"),
        ("S’abonner", "abonner"),
        ("Cerrar sesión", "cerrar sesión"),
        ("Salva modifiche", "salva"),
        ("Terminar sessão", "terminar sessão"),
        ("Verwijder", "verwijder"),
        ("Usuń konto", "usuń"),
        ("Удалить аккаунт", "удалить"),
        ("Від’єднати", "від’єднати"),
        ("Çıkış Yap", "çıkış yap"),
        ("アカウントを削除", "削除"),
        ("删除账户", "删除"),
        ("刪除", "刪除"),
        ("계정 삭제", "삭제"),
        # A word decomposed into a letter and its accent is the word it composes.
        ("Re\u0301initialiser", "r\u00e9initialiser"),
        # What only looks alike is not refused in other languages either.
        ("Gelöschte Objekte", None),
        ("Gesendet", None),
        ("저장됨", None),
        ("Abbrechen", None),
    ],
)
def test_the_words_of_other_languages_are_kept_off_beside_englishs(label: str, matched: str | None) -> None:
    assert matched_word(label, GUARD.words) == matched


def test_languages_are_named_by_code_and_english_or_one_without_words_adds_none() -> None:
    assert base("zh-Hant-TW") == "zh" and base(" DE ") == "de"
    assert words_in("de, de-AT") == observe_languages.WORDS["de"]
    assert words_in("en, sv") == words_in("") == ""
    assert ObserveGuard.of(True, "delete", confirmed=False) == ObserveGuard(("delete",))
    german = ObserveGuard.of(True, "", confirmed=False, languages="de")
    assert german is not None and matched_word("Löschen", german.words) and not matched_word("Supprimer", german.words)
    languages = find("agent.observe_languages")
    assert languages is not None and languages.rule.parse(" de ") == "de"
    assert languages.rule.describe().startswith("codes of languages there are words for")


def row(label: str, width: float, height: float, kind: str = "cell", **more: object) -> Element:
    frame = Frame(16, 300, width, height)
    return Element("e4", kind, label, None, 16 + width / 2, 300 + height / 2, frame=frame, **more)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("under", "start", "end", "found"),
    [
        # A sideways swipe on a list row can delete it, whichever way it goes, and whether the reader calls the row a
        # cell or the button that spans it.
        ([row("Milk", 370, 52)], (300, 326), (60, 326), ROW),
        ([row("Milk", 370, 52)], (100, 326), (340, 326), ROW),
        ([row("General", 370, 52, "button")], (300, 326), (60, 326), ROW),
        ([row("Apple Account", 370, 90, "button")], (300, 345), (60, 345), ROW),
        # Up or down scrolls, and from the left edge goes back.
        ([row("Milk", 370, 52)], (201, 326), (180, 100), None),
        ([row("Milk", 402, 52)], (10, 326), (300, 326), None),
        # A card, a chip or a page in a carousel is no row, nor a field, a slider or a line of text.
        ([row("Daily mix", 300, 200)], (300, 400), (60, 400), None),
        ([row("Jazz", 80, 32, "button")], (90, 316), (20, 316), None),
        ([row("Title", 370, 34, "field")], (300, 317), (60, 317), None),
        ([row("Volume", 370, 30, "slider")], (300, 315), (60, 315), None),
        ([row("Welcome to the app, swipe to see more", 370, 60, "text")], (300, 330), (60, 330), None),
        ([Element("e9", "cell", "Read without its edges", None, 201, 326)], (300, 326), (60, 326), None),
        # Words come first, from the label, the identifier or a custom action -- of anything under the swipe.
        ([row("Slide to pay", 300, 60, "slider")], (40, 330), (300, 330), "pay"),
        ([row("Milk", 370, 52, actions=("Archive", "Delete"))], (300, 326), (60, 326), "delete"),
        ([row("Card", 200, 200, "image", actions=("Remove",))], (150, 400), (20, 400), "remove"),
        ([row("Milk", 370, 52, actions=("Archive", "Flag"))], (300, 326), (60, 326), ROW),
    ],
)  # fmt: skip
def test_a_sideways_swipe_on_a_row_or_what_a_word_names_acts_on_it(
    under: list[Element], start: tuple[float, float], end: tuple[float, float], found: str | None
) -> None:
    hit = GUARD.swiped(under, start, end)
    assert (hit[1] if hit else None) == found
    assert hit is None or hit[0] is under[0]


def test_return_presses_what_a_word_names_on_the_screen_unless_it_searches() -> None:
    field = Element("e1", "field", "To", None, 201, 100)
    search = Element("e2", "search", "Search", None, 201, 60, flags=("editing",))
    send = Element("e3", "button", "Send", None, 360, 40)
    words = Element("e4", "text", "Please send the photos", None, 201, 300)
    heading = Element(None, "heading", "Save", None, 201, 20)
    send_key = Element("e5", "button", "send", None, 350, 786, identifier="Send", key=True)
    delete_key = Element("e6", "button", "delete", None, 370, 732, identifier="delete", key=True)
    assert GUARD.submitted([field, send], field) == (send, "send")
    assert GUARD.submitted([field, send_key, delete_key], None) == (send_key, "send")
    # Text, a heading and the keyboard's delete key are not what return presses.
    assert GUARD.submitted([field, words, heading, delete_key], field) is None
    # Return in a search field searches: typed into one, or with one being edited.
    assert GUARD.submitted([search, send], search) is None
    assert GUARD.submitted([search, send], None) is None
    assert GUARD.submitted([search, send], field) == (send, "send")


def test_each_refusal_says_what_was_not_played_and_how_to_go_on() -> None:
    milk, trash = row("Milk", 370, 52), button("", "trash")
    swiped = GUARD.swipe_refusal('e4 "Milk" (201,326)', milk, ROW)
    assert swiped == (
        'a sideways swipe from e4 "Milk" (201,326) can act on a row, whose swipe actions can delete it, and agents '
        "only observe here (agent.observe): it was not swiped. To scroll, swipe up or down. If the person you work for "
        "asked for exactly this, call again with confirm: true"
    )
    assert GUARD.swipe_refusal("(300,326)", milk, "delete").startswith(
        'a sideways swipe from (300,326) on "Milk" can act on it ("delete"), and agents only observe here'
    )
    assert GUARD.drop_refusal("a drag to (100,100)", trash, "trash").startswith(
        'a drag to (100,100) ends on "trash", which looks like it changes something ("trash"), and agents only '
        "observe here (agent.observe): it was not dragged."
    )
    send = Element("e3", "button", "Send", None, 360, 40)
    assert GUARD.submit_refusal("submit", send, "send").startswith(
        'submit presses return, which may press e3 "Send" on this screen, and that looks like it changes something '
        '("send"); agents only observe here (agent.observe): it was not played.'
    )
    assert GUARD.refusal("(100,100)", trash, "trash").startswith('(100,100) on "trash" looks like it changes')


def test_a_button_its_app_styles_destructive_is_refused_whatever_it_says() -> None:
    yes, keep = button("Yes", destructive=True), button("Keep")
    assert GUARD.matched([keep, yes]) == (yes, DESTRUCTIVE)
    # What it says is said first, when a word names it.
    assert GUARD.matched([button("Delete", destructive=True)]) == (button("Delete"), "delete")
    assert GUARD.swiped([yes], (150, 100), (20, 100)) == (yes, DESTRUCTIVE)
    assert GUARD.submitted([yes], None) == (yes, DESTRUCTIVE)
    assert GUARD.refusal('e1 "Yes" (100,100)', yes, DESTRUCTIVE) == (
        'e1 "Yes" (100,100) is a button its app styles destructive, and agents only observe here (agent.observe): it '
        "was not touched. If the person you work for asked for exactly this, call again with confirm: true"
    )
    assert GUARD.swipe_refusal("(150,100)", yes, DESTRUCTIVE).startswith(
        'a sideways swipe from (150,100) on "Yes" can act on a button its app styles destructive, and agents'
    )
    assert GUARD.drop_refusal("a drag to (100,100)", yes, DESTRUCTIVE).startswith(
        'a drag to (100,100) ends on "Yes", which is a button its app styles destructive, and agents only observe'
    )
    assert GUARD.submit_refusal("press return", yes, DESTRUCTIVE).startswith(
        'press return presses return, which may press e1 "Yes" on this screen, and that is a button its app styles '
        "destructive; agents only observe here"
    )
