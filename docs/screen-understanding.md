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
  `keyboard`, or `bar` for one nothing names). A touch that would begin on it is refused with how to reach it -- scroll it into view -- rather than
  played on the bar. The bar's own buttons are on it, not under it.
- At most `agent.snapshot_max_elements` lines (120 by default).

The source is the device's accessibility tree, read by the native helper or idb_companion, so an app whose controls have accessibility labels reads
best. A screen accessibility says nothing about is [read from its pixels](#read-from-pixels) instead.

Where a bar is comes from the same tree. Xcode's hierarchy, WebDriverAgent and the app SDK name tab bars, toolbars,
navigation bars and the keyboard. Accessibility -- idb_companion and the native helper -- does not: it lists each bar
as a group with nothing in it, and the keyboard as its keys. A tab bar and a toolbar are known by the name UIKit gives
them, in whichever of its languages the device is in -- "Tab Bar", "Tab-Leiste", "Barre d’onglets", "タブバー"; "Toolbar",
"Symbolleiste", "ツールバー" -- and a navigation bar by how its role is described: "Nav bar", "Navigationsleiste",
"ナビゲーションバー". The names are UIKit's own, from the iOS 18.6, 26.5 and 27.0 simulators. A group drawn as a bar is --
labelled, holding nothing, across the whole foot of the screen, 44 to 100 points tall -- is one too, for a name iOS has
not used yet, and says `covered by bar`. A bar iOS 26 frames as the whole screen -- Settings' toolbar through Xcode's
hierarchy -- covers only where its controls are drawn.

What accessibility does not list covers nothing, and it does not always list a bar (measured on iOS 26.5 in Health,
Files, Photos, Settings, Shortcuts and Messages, in English, German, French and Japanese, 2026-10-16):

- **Settings lists no navigation bar** -- only its back button and title -- though one is there: a row scrolled under
  it is listed as if it were clear. Health lists none either. Files, Photos and Shortcuts list theirs.
- **Settings lists its toolbar or the search field on it**, one or the other, from one read to the next: while it lists
  only the field, the rows beside and below the search capsule are not known to be under the toolbar.
- **A button drawn in front of a bar but not on it** -- one floating over a tab bar -- is taken for what is under the
  bar: accessibility lists elements in the order a person reads them, not the order they are drawn, so nothing says
  which is in front. None of the stock apps measured has one: Health's and Photos' search button is part of their tab
  bar, and is not listed. A tap by point still reaches it.

A reader merged in -- [Xcode's hierarchy](connectors.md#merging-xcodes-hierarchy), the app's
[own](connectors.md#merging-an-apps-own-hierarchy), the screen's pixels -- adds what accessibility left out, and a bar
covers that too: a row only the app SDK lists, half under idb's tab bar, says so. What a reader merged in finds wholly
on a bar is taken for the bar's own -- the app's tabs on idb's tab bar, which lists none of them -- and is not covered,
so neither is something it finds wholly under one.

## An app turned on its side

A snapshot is of the app as it is drawn. An app turned to landscape is read in its own points, and the first line says
so -- `iOS 26.5 · Game · 874x402pt landscape · #4c1e` on an iPhone 17 Pro -- and so is everything an agent gives back:
the points and directions of `sim_act` (`"up"` is up the app), and a `sim_screenshot` and its `region`, which come back
upright. `sim_device info` gives the same size. A touch lands where that point is on the device's screen, which does
not turn: the viewer shows the screen as the device has it, with the app on its side -- even when Simulator.app's
window is turned with it.

The same holds for an app upside down (`1032x1376pt upside down` on an iPad Pro 13-inch), and whether it is the app
that turned or the device, as Simulator.app's Device › Rotate turns it.

### Turning the simulator

`sim_device rotate` turns a simulator the way Device › Rotate does, so an agent can check a landscape layout without
a person turning it. `to` is the way the **app** should lie, in the words a snapshot uses -- `portrait`,
`landscape_left`, `landscape_right` or `upside_down` -- not the way UIKit names the device: a device turned landscape
left shows its app landscape right, and the native helper sends the device's orientation that gives the app's. There
are no relative turns: say where the app should end up. `landscape_left` puts the app's top along the screen's left
edge, `landscape_right` along its right edge.

The answer says how the app on screen was then read lying, and what changed since the agent last looked, as `sim_act`
does: `turned to landscape_right: the app on screen lies landscape_right · 874x402pt`, then the new lines. It reads
the screen until the app lies as asked -- two seconds at most -- and then waits for it to stop moving, since UIKit
drops touches while an app turns, so the refs it answers with can be tapped at once. An app that does not turn that
way is said to stay as it was: one that runs only upright stays portrait, and an iPhone without a Home button is never
upside down. The device stays turned until it is turned back, so the next app opened that can turn will. Every
agent's last look on the device is read again before one of its refs is touched.

Only the native connector can turn a device -- its helper sends the GraphicsServices event Simulator.app sends, to the
device's `PurpleWorkspacePort` -- and only a simulator: a real device is turned in the hand, and idb_companion and
simctl have no way to. Where the connector cannot, `rotate` is refused saying so, and a native helper built before it
could says to build it again. Measured on iOS 26.5 (2026-10-16), on an iPhone 17 Pro and an iPad Pro 13-inch (M5)
with an app that takes every orientation: each of the four ways was read as asked (upside down on the iPad only), a
ref tapped straight after each turn landed, and Settings stayed portrait. A turn the app follows answered in 0.9 to 1.9
seconds, one it does not in about 2.4. The first turn after a simulator boots can leave the app slow to answer a touch
for a moment: a `wait` on the next `sim_act` covers it.

Which way the app is turned is found each time the screen is read, the same way by the native helper and by the idb
connector: sideways or upright from the shape of the app's tree, which is in the app's points, and which of the two
ways by asking what is at a point of the screen -- the middle of one of the app's small elements, where each way would
put it, and only the right way finds that element there. idb_companion does not say which way an app is turned, so the
idb connector asks it about two points at once; that adds about 10 to 20 ms to a read. Measured with the simulator
turned both ways in Safari, Photos, Calendar, Maps, Files, Messages, Contacts and an app that turns itself, every read
told left from right. When nothing tells within half a second -- a permission alert can answer no point at all -- the
way the last read found is kept, and only without one is the likelier way taken; [a system
alert](#a-system-alert-over-a-turned-app) is then read the way it is drawn. [Text read from the screen's
pixels](#read-from-pixels) is read from a screenshot turned upright first, so it reads as the app shows it and merges
in at the app's points, and the focused field's row a settle wait leaves out is the app's row, wherever it lies on the
screen.

Measured on iOS 26.5 simulators, an iPhone 17 Pro and an iPad Pro 13-inch (M5) (2026-10-16), with an app that takes
every orientation, turned both ways and, on the iPad, upside down, by sending each device the orientation event
Simulator.app sends:

- **The screen does not turn.** The framebuffer stayed portrait -- 1206x2622 and 2064x2752 -- with the app drawn on its
  side or upside down in it, exactly as when an app turns only its own interface, and touches stayed in its portrait
  points. idb_companion's screenshots are that framebuffer too.
- **Each way was named right**, every tap on a ref landed on its element, and a screenshot and a region came back
  upright, through the native helper.
- **An app that takes only portrait**, such as Settings, stays upright on a turned device, and is read so.
- **An iPhone without a Home button does not turn an app upside down**: turned so, the app keeps the way it was.
- `simctl io screenshot`, which the simctl connector shows, turns its picture by the device's orientation itself:
  there a device turned through Simulator.app comes out upright, sized as it is drawn -- 2752x2064 on the iPad, and
  2622x1206 on the iPhone. The simctl connector reads no tree, so it cannot say which way the app is turned; text
  [read from its pixels](#read-from-pixels) is read on a screen of the picture's own shape instead: the snapshot says
  `874x402pt`, a line is where it is in that picture, and `sim_device info` gives the size the last snapshot read. A
  `region` of a screenshot needs another connector there, turned or not.

### A system alert over a turned app

A permission alert -- Maps asking for the location, an app asking to send notifications -- is the system's, drawn the
way the device is turned, but accessibility reads it, through idb_companion and the native helper alike, in the
screen's own portrait points: on an iPhone 17 Pro turned left, Maps' alert is drawn landscape right, and its title is a
strip 42x260 down the screen's right edge. Its root, which has no name, was sometimes the screen's shape and sometimes
the turned one, so the way found from it said portrait, or landscape the wrong way round.

Such an alert is read as it is drawn: its buttons are wider than they are tall and its elements come in reading order
-- the title, the message, then the buttons, side by side left to right -- so the one way of the four that lays its
frames out so is the way it is drawn, and its frames are put in that way's points. The snapshot then says `874x402pt
landscape`, the title comes first and the buttons below it, a screenshot comes back with the alert upright, and the
text read from its pixels is read upright too. A tap on a button lands where it did before.

Measured on iOS 26.5 (2026-10-16), the device turned as Simulator.app turns it: Maps' location alert and an app's
notification alert, on an iPhone 17 Pro turned both ways and an iPad Pro 13-inch turned both ways and upside down,
through both readers; every way was told, and a tap at a button's point in the snapshot took the alert away, on the
iPhone either way and on the iPad turned left. An alert that no single way lays out -- a square button, buttons out of
reading order, more than 12 elements -- is left as it was read, its frames the screen's points: a touch still lands,
but the snapshot is of the screen upright. An app's own alert is in the app's points already, and is read with the app.

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
- **An app on its side or upside down is read upright.** The screenshot is turned the way the tree says the app is
  before it is read, so its text is read the right way up and each line is where the app shows it, in its own points.
  simctl's picture of a turned device is upright already, and is read in its own shape.

### Seeing what was read

With **`perception.ocr_overlay`** on, the viewer outlines each line a reading found -- dashed where it was unsure -- and
pointing at a box says what it reads and how sure. A line of an app on its side is outlined where it is on the screen
the viewer shows, so its box stands up. The boxes go when they no longer hold: before an agent's gesture,
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
  caret does not count as moving: its row is left out, across the app as it is drawn, even on its side.
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

  A button its app styles **destructive** is refused whatever it says -- "Yes", "Continue", a word in a language no
  list has -- by all of the above, and its refusal says "is a button its app styles destructive". Only an app that
  links the [app SDK](app-sdk.md#destructive-buttons) can tell: accessibility reads a `UIAlertAction` styled
  `.destructive` as any other button, with the same traits as the alert's Cancel, and neither idb_companion, the
  native helper nor Xcode's hierarchy says anything more of it. The SDK marks an alert's or an action sheet's
  destructive actions -- a SwiftUI `.alert` or `.confirmationDialog`'s `Button(role: .destructive)` too -- and, while
  it reads SwiftUI's debug data, a `Button(role: .destructive)` drawn in place; the snapshot marks the element
  accessibility read in the same place. Without the SDK such a button is refused by what it says, and one whose label
  and identifier say nothing of the kind is not caught; neither is an element no reader names at all. It is a guard
  rail against a slip, not a lock: the agent is told it may confirm when the person asked for exactly that.
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
