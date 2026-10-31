# SPDX-License-Identifier: Apache-2.0
"""A process-only adapter never acquires a global fallback when an app refuses input."""

import pytest

import check_mac_confinement as guard


def test_repository_stays_app_scoped():
    assert guard.main() == 0


@pytest.mark.parametrize(
    "call",
    [
        "CGEventPost(event)",
        "CGEventPostToPid(pid, event)",
        "CGWarpMouseCursorPosition(point)",
        "CGAssociateMouseAndMouseCursorPosition(true)",
        "AXUIElementCreateSystemWide()",
        "event.post(tap: .cghidEventTap)",
        "event.postToPid(pid)",
        "NSApp.activate()",
        "window.makeKeyAndOrderFront(nil)",
        "SCContentFilter(display: display, including: apps)",
    ],
)
def test_global_fallbacks_fail_the_gate(tmp_path, capsys, call):
    folder = tmp_path / guard.FOLDERS[0]
    folder.mkdir(parents=True)
    (folder / "Fallback.swift").write_text(call)
    assert guard.main(tmp_path) == 1
    assert "Fallback.swift:1:" in capsys.readouterr().err


def test_layout_constraints_are_not_application_activation():
    assert guard.PATTERN.search("NSLayoutConstraint.activate([constraint])") is None
