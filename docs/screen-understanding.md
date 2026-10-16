# Screen understanding

An agent needs to know what is on the screen, and every token it reads costs time and money. SimMirror gives it the
screen as a few short lines of text, and a screenshot only when a question is visual.

## A snapshot

`sim_snapshot` on the Settings app answers:

```
iOS 26.5 · Settings · 402x874pt · #bb14
[heading "Settings"]
e1 button "Apple Account, Sign in to access your iCloud data, the App …" (201,213)
e2 button "General" (201,319)
e3 button "Accessibility" (201,371)
e4 button "Action Button" (201,423)
e5 button "Apple Intelligence & Siri" (201,475)
e6 button "Camera" (201,527)
e7 button "Home Screen & App Library" (201,579)
e8 button "Search" (201,631)
e9 button "StandBy" (201,683)
e10 button "Screen Time" (201,770)
e11 button "Passcode" (201,853)
e12 search ="Search" (201,822)
e13 button "Dictate" (344,822)
```

- **The first line** is the runtime, the app, the screen's size in points, and the **digest** (`#bb14`), which changes
  whenever what the screen says changes.
- **One line per element** that can be acted on or says where things are -- buttons, fields, switches, cells, text,
  headings. Containers that only hold others are left out, and so is anything wholly off screen. A label longer than
  60 characters is cut with `…`.
- **A ref** (`e2`) names an element across snapshots.
- **The point** after it is where a tap lands: the middle of the part of the element that is on screen, and clear of
  any bar in front of it.
- **What a bar covers says so.** A list scrolls on under a tab bar, a toolbar, a navigation bar and the keyboard, and
  a tap there lands on the bar. An element partly under one is tapped in the part that shows; one with no more than a
  sliver showing is still listed, with its ref, and ends `covered by tab bar` (or `toolbar`, `navigation bar`,
  `keyboard`). A touch that would begin on it is refused with how to reach it -- scroll it into view -- rather than
  played on the bar. The bar's own buttons are on it, not under it.
- At most `agent.snapshot_max_elements` lines (120 by default).

The source is the device's accessibility tree, read by the native helper or idb_companion, so an app whose controls have accessibility labels reads
best. A screen accessibility says nothing about is [read from its pixels](#read-from-pixels) instead.

Where a bar is comes from the same tree. Xcode's hierarchy, WebDriverAgent and the app SDK name tab bars, toolbars,
navigation bars and the keyboard. idb_companion lists a tab bar as a group UIKit names "Tab Bar" -- in English: a
device in another language names it in that language, and is not known by it -- and each key of the keyboard, but no
toolbar, and a navigation bar too seldom to rely on, so those are not known there. A bar iOS 26 frames as the whole
screen -- Settings' toolbar -- covers only where its controls are drawn.

## An app turned on its side

A snapshot is of the app as it is drawn. An app turned to landscape is read in its own points, and the first line says
so -- `iOS 26.5 · Game · 874x402pt landscape · #4c1e` on an iPhone 17 Pro -- and so is everything an agent gives back:
the points and directions of `sim_act` (`"up"` is up the app), and a `sim_screenshot` and its `region`, which come back
upright. `sim_device info` gives the same size. A touch lands where that point is on the device's screen, which does
not turn: the viewer shows the screen as the device has it, with the app on its side.

Which way the app is turned is found each time the screen is read, by the native helper. idb_companion does not say,
so with the idb connector an app on its side is read as if it were upright. Text read from the screen's pixels is not
merged into a snapshot of an app on its side.

## Refs

A ref stays with its element for as long as the element is on screen -- the same kind, label and identifier -- even
after it moved, so an agent can snapshot, scroll, and still tap `e2` where `e2` is now. A ref is never given to a
different element later. When one of several elements that look the same is added or removed, all of them get fresh
refs: a ref an agent kept for one of them is refused as not on screen, never landing on its neighbour.

## Diffs

`sim_snapshot` with `mode: "diff"` (the default) gives only what changed since this agent last looked:

```
#bb14 → #7ba8
~ e2 button "General" (201,419)
- e6 button "Camera" (201,527)
```

`+` is an element that appeared, `-` one that went, and `~` one that moved or changed. When most of the screen changed
-- a new screen, a long scroll -- the whole screen reads better than a list of everything that moved, so the diff gives
the whole screen instead.

## Read from pixels

A game, a canvas, an app still loading, an app whose accessibility stopped answering, or a device shown through a
connector that reads no tree at all (simctl) still shows text. When the tree says nothing, a snapshot reads the text in
the screen's pixels with macOS's Vision:

```
iOS 26.5 · 402x874pt · #6af8
e1 text "Sign in" (200,410)
e2 text "Forgot password?" (200,470)
accessibility said nothing on this screen, so its pixels were read -- if the app has controls, its accessibility may have stopped answering, as it can after UI tests: sim_device restart brings it back
2 lines were read from the screen's pixels: text may be misread, and a ref taps its middle
```

- **Each line of text is a `text` element with a ref**, so `{"tap": "e1"}` taps the middle of "Sign in" and
  `{"for": "Sign in"}` waits for it.
- **`perception.ocr` says when.** `fallback` (the default) reads pixels only when the tree says nothing, and lets a
  device whose connector reads no tree be read at all. `merge` reads them on every snapshot and adds the text the tree
  leaves out, as [Xcode's hierarchy](connectors.md#merging-xcodes-hierarchy) does -- 0.3 to 1 second more each. `off`
  never reads them.
- **How it reads** is the scope's too: `perception.ocr_level` (`accurate`, or `fast`), `perception.ocr_languages`
  (codes such as `en-US, uk-UA`; empty to detect them), `perception.ocr_correction`, `perception.ocr_min_confidence` and
  `perception.ocr_timeout_ms` -- all in the settings panel's **Screen reading** tab.
- **The reader is SimMirror's own**: a small Swift helper it compiles with the scope's Xcode the first time a screen is
  read, and keeps under its state folder until a new SimMirror or a new Xcode compiles it again. `sim-mirror doctor`
  compiles it and reads a test picture. Measured on macOS 26.6 (2026-09-17): the first compile took 6 to 7 seconds with
  Xcode 26.6 and with 27.0; an 804x1748 screenshot then read in 0.44 seconds at `accurate` and 0.05 at `fast`.
- **A screen that did not change is not read again**, and reads the same, refs included.

### Seeing what was read

With **`perception.ocr_overlay`** on, the viewer outlines each line a reading found -- dashed where it was unsure -- and
pointing at a box says what it reads and how sure. The boxes go when they no longer hold: before an agent's gesture,
when a person touches the screen, and when a snapshot reads the screen without its pixels. Like the agent's cursor, they
are drawn by the viewer over the screen, so no screenshot or recording of the device shows them.

## Acting: `sim_act`

![The viewer's cursor gliding to a row, named for the agent driving it, and the screen changing after the tap](media/cursor.gif)

Every step is announced before it lands and drawn where it will land, so anyone watching sees what is coming.

One call plays a batch of up to 20 steps, because a round trip is what an agent pays for, not a gesture:

```json
{
  "steps": [
    {"tap": "e2"},
    {"swipe": {"from": [201, 600], "direction": "up"}},
    {"type": "Groceries", "into": "e7", "clear": true, "submit": true},
    {"press": "home"}
  ],
  "wait": {"for": "Saved"}
}
```

- Steps: `tap`, `long_press`, `swipe`, `drag`, `type`, `press`, `pause`. A target is a ref or a point `[x, y]`. A
  swipe (`from`, then `to` or `direction`) and a drag (`path`) each take how long they last in `ms`; a key a step does
  not take is refused, inside a swipe or a drag too. A drag moves as soon as it touches: `long_press` is what holds.
- **Waits** after the steps: `{"for": text}` until text appears, `{"gone": text}` until it goes, or
  `{"settle_ms": ms}` until the screen stops moving; each takes an optional `timeout_ms`. A focused field's blinking
  caret does not count as moving.
- **Settling survives animations that never stop.** A settle wait compares a coarse grid of the screen's brightness
  with the look its quiet time began with (`perception.settle`: `perceptual`, the default). Small places that keep
  changing look after look -- a spinner, a pulsing dot, a shimmer -- are noticed and no longer watched, and the answer
  says so: `settled after 450ms (3 small places kept moving and were not watched)`. A label that changes once or a row
  that appears still starts the quiet time again, and a slow fade is not taken for stillness. Text inside a place that
  kept moving -- an animated "Saving…" -- is not watched either, so wait `{"for": "Saved"}` there.
  `perception.settle_tolerance` and `perception.settle_grid` tune it; `exact` waits until not one byte of a screenshot
  changes, as SimMirror 1.0 did.
- **The answer** says ok or why not for each step, then what changed on screen as a diff. A step that cannot be played
  -- a ref whose element is gone -- ends the batch; the steps before it stay done. A ref after a step that touched
  the screen is looked up again first, so one a keyboard now covers, or a new screen took away, is refused rather
  than tapped where it was.
- **Observe mode** (`agent.observe`, off by default, per scope) is for a walkthrough on an account the agent must not
  change -- a shared test account, a person's own phone. What reads like it changes something -- an element whose
  label or identifier holds one of `agent.observe_words` (Delete, Remove, Erase, Reset, Sign out, Log out, Unlink,
  Unpair, Save, Apply, Submit, Send, Confirm, Purchase, Buy, Pay, Subscribe, the trash icon and a few more) or of the
  words of `agent.observe_languages` -- is kept off. Refused, found in the agent's last snapshot:

  - a `tap`, a `long_press` or a `type` `into` such an element, by its ref or by a point its frame holds;
  - a sideways `swipe`, or a `drag`, that starts on one, on a row whose custom actions are one (idb_companion lists a
    row's swipe actions, "Delete"; the other readers do not), or on any list row: a cell, or the button spanning a
    row, at least 240 points across and three times as wide as it is tall. A swipe up or down scrolls and one from the
    left edge goes back, so neither is refused; a sideways swipe on a carousel's card, a chip or a page is not either;
  - a `drag` that ends on such an element: a drop onto the trash;
  - `submit`, or `press` `return`, while a button, a link or a key on the screen is one -- a form's Send, the
    keyboard's own send key -- since return may press it. Return in a search field searches, so it is never refused
    there.

  A call that says `confirm: true` plays them, and its line says so; the next call is guarded again.

  Words are matched whole, ignoring case: `save` refuses "Save changes" but not "Saved items", and `log out` also
  refuses "Logout" and "Log-out". An identifier is read as the words its developer ran together, so `delete` refuses
  `deleteAccountButton`, and `trash` an icon-only button iOS labels or identifies by its SF Symbol, `trash.fill`. The
  keyboard's own delete key is not a delete. Scripts without spaces, such as Chinese or Japanese, match anywhere in a
  label, so their words also refuse what only contains them ("保存された写真" holds 保存, "save").

  Besides English, the words of German, Spanish, French, Italian, Japanese, Korean, Dutch, Polish, Portuguese,
  Russian, Turkish, Ukrainian and Chinese are matched at once, since an app is not always shown in its device's
  language. They were chosen by hand, kept to the words buttons use and leaving out those that say much else
  (Chinese 应用 is "apply" and "app"), and are not a translator's: an app's own words, or a language not there, go in
  `agent.observe_words`, and `agent.observe_languages` narrows the list where a word refuses too much.
  Accessibility does not say an alert's button is destructive -- a `UIAlertAction` styled `.destructive` reads as any
  other button -- so such a button is refused by what it says, and one whose label and identifier say nothing of the
  kind is not caught; neither is an element no reader names at all. It is a guard rail against a slip, not a lock: the
  agent is told it may confirm when the person asked for exactly that.
- **Every gesture is announced before it lands**: viewers draw the agent's cursor moving there first. **A person comes
  first**: agents wait for a person's hand to be still before a gesture, and take turns with each other.
- **The cursor stays while the agent works**: it rests, dimmed, where the agent last acted -- through the agent's
  thinking, a build or a test run -- and leaves `agent.cursor_linger_s` (60 seconds) after its last tool call; `0` lets
  it go once each gesture is drawn. It is the viewer's own, drawn over the screen: `sim_screenshot`, snapshots and
  anything recorded from the device never contain it.

## Screenshots: `sim_screenshot`

For what text cannot tell: colour, layout, an image, an animation.

- `width` -- 160 to 1200 pixels; `agent.screenshot_width` (400) by default, which is plenty to judge a layout.
- `region` -- a ref or `{x, y, w, h}` in points, to see one part.
- `frames` (up to 6) `interval_ms` apart, to see something move.

## What it costs

Tools estimate what an answer costs an agent -- text at about 4 characters a token, an image at about 750 pixels a
token. Models count their own way, so these are estimates, for comparing answers with each other.

![The same Settings screen twice: a 400-pixel screenshot beside the snapshot of it, thirteen lines of text](media/snapshot-vs-screenshot.png)

Measured on the screen above: the snapshot is 516 characters -- about 112 tokens -- against about 469 for a
400-pixel-wide screenshot of it, and about 4181 for a 1200-pixel one. A diff of a screen that has not changed is
83 characters.

Measured through the MCP relay on Settings → General (iPhone 17 Pro simulator, iOS 26.5, idb, 5 calls each,
2026-09-16): a full snapshot answered in 561 bytes, about 112 tokens (p50 65 ms); a diff of an unchanged screen in
83 bytes, about 5 tokens; a 400-pixel-wide screenshot in 62 KB, about 469 tokens (p50 8 ms); a 1200-pixel-wide one
in 276 KB, about 4181 tokens. Measure your own screens with [the token budget example](../examples/token-budget/).

## The loop agents are taught

1. `sim_snapshot` to read.
2. `sim_act` with every step already known, and a wait.
3. Read the diff it answers with; act again.
4. `sim_screenshot` only to check how something looks.

The MCP instructions and the Claude Code plugin's skill both teach it.

## What comes next

The element tree comes from a `TreeReader`: the accessibility tree the native helper or idb_companion reads, Xcode 27's UI hierarchy through
`mcpbridge`, the view hierarchy an app shares through [its debug SDK](app-sdk.md), or the text in the screen's pixels.
Readers merge -- the first one's tree whole, each later one adding only what the ones before did not say in the same
place -- or fall back, a later one read only when the ones before read nothing. A later reader can also name: an app's
hierarchy names what accessibility found without a label, so an element renamed that way gets a new ref once. Xcode's
hierarchy can already be [merged into idb's](connectors.md#merging-xcodes-hierarchy), for the text and links of a web
page, an app's [its own](connectors.md#merging-an-apps-own-hierarchy), and pixels read when nothing else can. Planned
readers merge in more -- a WebDriverAgent source tree on real devices. See the [roadmap](../ROADMAP.md).
