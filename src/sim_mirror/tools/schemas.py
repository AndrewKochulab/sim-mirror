# SPDX-License-Identifier: Apache-2.0
"""Every agent tool's description and input schema, and the instructions an MCP client is given -- the one catalogue.

The tool names are part of SimMirror's public surface: an agent's prompts, a plugin's skill and a host's permissions
name them. `scripts/gen_docs.py` writes `docs/reference/tools.md` from here.
"""

from __future__ import annotations

from typing import Any

from sim_mirror.build.xcodebuild import TESTS_MAX

LAUNCH_ARGS_MAX = 20
LOG_SINCE_S = (1, 300)
LOG_LINES = (1, 200)
#: How long a build call waits before answering with its build id; within a relay's own limit for a call.
BUILD_WAIT_S = 120
BUILD_WAIT_BOUNDS = (0, 600)


def schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_BUILD_PROPERTIES: dict[str, Any] = {
    "scheme": {"type": "string", "description": "needed only when the project has several"},
    "project": {"type": "string", "description": "a .xcodeproj in your folder, when it has several"},
    "workspace": {"type": "string", "description": "a .xcworkspace in your folder, when it has several"},
    "configuration": {"type": "string", "description": "Debug unless the settings chose another"},
    "wait_s": {"type": "integer", "minimum": BUILD_WAIT_BOUNDS[0], "maximum": BUILD_WAIT_BOUNDS[1]},
    "build_id": {"type": "string", "description": "a run that answered still running: wait for it again"},
}
_TEST_IDS = {
    "type": "array",
    "items": {"type": "string"},
    "maxItems": TESTS_MAX,
    "description": "test identifiers like AppTests/LoginTests/testLogin",
}

#: Each tool's description and input schema, by name, in the order a manifest lists them.
SCHEMAS: dict[str, tuple[str, dict[str, Any]]] = {
    "sim_device": (
        "Your iOS Simulator. info: which device, its state and the xcodebuild destination (does not start it). boot: "
        "start it and wait until it is ready. restart: shut it down and start it again -- for when its apps stop "
        "answering sim_snapshot, as they can after UI tests. appearance: light or dark.",
        schema(
            {
                "action": {"type": "string", "enum": ["info", "boot", "restart", "appearance"]},
                "mode": {"type": "string", "enum": ["light", "dark"]},
            }
        ),
    ),
    "sim_snapshot": (
        "What is on screen as short lines -- one per button, field, text or heading, each with a ref (e4) and where a "
        "tap lands. Cheap: prefer it to a screenshot. mode diff (default) gives only what changed since you last "
        "looked; full gives the whole screen.",
        schema({"mode": {"type": "string", "enum": ["diff", "full"]}}),
    ),
    "sim_screenshot": (
        "A JPEG of the screen, or of a region (a ref, or {x, y, w, h} in points). frames > 1 takes that many, "
        "interval_ms apart, to see an animation. Costs far more than sim_snapshot: use it to check how something "
        "looks.",
        schema(
            {
                "width": {"type": "integer", "minimum": 160, "maximum": 1200},
                "region": {"oneOf": [{"type": "string"}, {"type": "object"}]},
                "frames": {"type": "integer", "minimum": 1, "maximum": 6},
                "interval_ms": {"type": "integer", "minimum": 50, "maximum": 2000},
            }
        ),
    ),
    "sim_act": (
        "Touch the device: a batch of steps in one call, played in order, each drawn live for anyone watching. "
        'Steps: {"tap": T}, {"long_press": T, "ms": 800}, {"swipe": {"from": T, "direction": "up"}} or {"swipe": '
        '{"from": T, "to": [x, y]}}, {"drag": {"path": [[x, y], ...]}}, {"type": "Groceries", "into": T, "clear": '
        'true, "submit": true} (the words to type are the value of "type"; typing adds to what a field already '
        'holds, "clear": true replaces it), {"press": "home" | "lock" | "return" | "delete" | ...}, {"pause": ms}. A '
        'key a step does not take is refused. T is a ref like "e4" or a point [x, y]. wait after the steps: {"for": '
        'text}, {"gone": text} or {"settle_ms": 400} for an animation. Answers ok/error per step, then what changed '
        "on screen.",
        schema(
            {
                "steps": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "object"}},
                "wait": {"type": "object"},
                "snapshot": {"type": "string", "enum": ["diff", "full", "none"]},
            },
            required=("steps",),
        ),
    ),
    "sim_app": (
        "Apps on the device. launch or terminate a bundle_id -- launching an app that is already running only brings "
        "it to the front, so give relaunch: true to start it fresh; install a built .app (a path in a folder you may "
        "install from, or DerivedData); open_url; logs: the device log for the last since_s seconds, for a bundle_id "
        "(else errors and faults), optionally filtered.",
        schema(
            {
                "action": {"type": "string", "enum": ["launch", "terminate", "install", "open_url", "logs"]},
                "bundle_id": {"type": "string"},
                "relaunch": {"type": "boolean"},
                "path": {"type": "string"},
                "url": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}, "maxItems": LAUNCH_ARGS_MAX},
                "since_s": {"type": "integer", "minimum": LOG_SINCE_S[0], "maximum": LOG_SINCE_S[1]},
                "filter": {"type": "string"},
                "lines": {"type": "integer", "minimum": LOG_LINES[0], "maximum": LOG_LINES[1]},
            },
            required=("action",),
        ),
    ),
    "sim_build_run": (
        "Build the app in your folder with xcodebuild for your simulator, then install and launch it. Answers ok, or "
        "each error as file:line and message. A build longer than wait_s answers with its build_id: call again with "
        "that build_id to keep waiting.",
        schema(_BUILD_PROPERTIES),
    ),
    "sim_test": (
        "Run the scheme's tests (unit and UI) on your simulator. Answers with the counts and each failure where it "
        "happened. While they run the device takes no sim_act steps.",
        schema({**_BUILD_PROPERTIES, "only_testing": _TEST_IDS, "skip_testing": _TEST_IDS}),
    ),
}

LOOK_AND_ACT = (
    "These tools drive your iOS Simulator while anyone watching sees every gesture live. Look with sim_snapshot "
    "(cheap text) before acting, then act with its refs in one sim_act batch; take sim_screenshot only to check how "
    "something looks or moves. The device boots on first use. "
)
SHELL_INSTRUCTIONS = (
    "Build from your shell with xcodebuild and the destination sim_device info gives, then sim_app install and launch."
)
BUILD_INSTRUCTIONS = (
    "Build, install and launch the app with sim_build_run, and run its tests with sim_test: both answer with only what "
    "failed and where."
)
