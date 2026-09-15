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
- **The point** after it is where a tap lands: the middle of the part of the element that is on screen.
- At most `agent.snapshot_max_elements` lines (120 by default).

The source is the accessibility tree idb_companion reads, so an app whose controls have accessibility labels reads
best.

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

## Acting: `sim_act`

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

- Steps: `tap`, `long_press`, `swipe`, `drag`, `type`, `press`, `pause`. A target is a ref or a point `[x, y]`.
- **Waits** after the steps: `{"for": text}` until text appears, `{"gone": text}` until it goes, or
  `{"settle_ms": ms}` until the screen stops moving; each takes an optional `timeout_ms`. A focused field's blinking
  caret does not count as moving.
- **The answer** says ok or why not for each step, then what changed on screen as a diff. A step that cannot be played
  -- a ref whose element is gone -- ends the batch; the steps before it stay done.
- **Every gesture is announced before it lands**: viewers draw the agent's cursor moving there first. **A person comes
  first**: agents wait for a person's hand to be still before a gesture, and take turns with each other.

## Screenshots: `sim_screenshot`

For what text cannot tell: colour, layout, an image, an animation.

- `width` -- 160 to 1200 pixels; `agent.screenshot_width` (400) by default, which is plenty to judge a layout.
- `region` -- a ref or `{x, y, w, h}` in points, to see one part.
- `frames` (up to 6) `interval_ms` apart, to see something move.

## What it costs

Tools estimate what an answer costs an agent -- text at about 4 characters a token, an image at about 750 pixels a
token. Models count their own way, so these are estimates, for comparing answers with each other. On the Settings
screen above, the snapshot is 558 characters of text -- about 140 tokens; a 400-pixel-wide
screenshot of the same screen is about 400×870 pixels -- about 464 tokens. Measure your own screens with [the token budget example](../examples/token-budget/).

## The loop agents are taught

1. `sim_snapshot` to read.
2. `sim_act` with every step already known, and a wait.
3. Read the diff it answers with; act again.
4. `sim_screenshot` only to check how something looks.

The MCP instructions and the Claude Code plugin's skill both teach it.

## What comes next

The element tree comes from a `TreeReader`; v0.1 has one, over idb_companion. Planned readers merge in more -- an
Xcode 27 `mcpbridge` hierarchy, a WebDriverAgent source tree on real devices, an in-app debug hierarchy for SwiftUI and
UIKit views without accessibility labels, OCR -- each only adding elements the first reader lacked. See the
[roadmap](../ROADMAP.md).
