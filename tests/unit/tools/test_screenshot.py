# SPDX-License-Identifier: Apache-2.0
"""`sim_screenshot` saved to a file for evidence -- only inside the folders an agent may write to -- and a screen that
is not live saying why in its caption."""

from __future__ import annotations

import base64
import dataclasses
import io
import stat
from pathlib import Path
from typing import Any

from PIL import Image

from sim_mirror.connectors.base import Capability, Shot
from sim_mirror.core.actions import AgentActions
from sim_mirror.seams import Caller
from sim_mirror.testing.checkouts import linked_worktree, main_checkout
from sim_mirror.testing.fakes import JPEG, FakeConnector, FakePhoneBackend, no_wait
from sim_mirror.testing.pictures import picture
from sim_mirror.testing.rig import VIEW_ONLY, DeviceRig, scope
from sim_mirror.tools.context import ToolContext
from sim_mirror.tools.registry import ToolRegistry
from sim_mirror.tools.results import Result, text
from sim_mirror.tools.schemas import SAVE_TO_MAX
from sim_mirror.tools.screenshot import screen_note

REGISTRY = ToolRegistry()
CALLER = Caller(scope("tp-1"), key="agent-1", title="Claude · notes")


def rigged(tmp_path: Path, **options: Any) -> DeviceRig:
    rig = DeviceRig(tmp_path, **options)
    rig.config.set(recording_folder=str(tmp_path / "Movies"))
    return rig


async def use(rig: DeviceRig, arguments: dict[str, Any], **changes: Any) -> Result:
    ctx = ToolContext(
        manager=rig.manager,
        actions=AgentActions(rig.manager, rig.config, clock=rig.clock, sleep=no_wait),
        caller=CALLER,
        config=rig.config.get(CALLER.scope),
        copy=rig.copy,
        sleep=no_wait,
    )
    return await REGISTRY.call("sim_screenshot", arguments, dataclasses.replace(ctx, **changes))


def said(result: Result) -> str:
    return str(result["content"][0]["text"])


def private(path: Path) -> bool:
    return stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_a_screenshot_is_saved_in_the_scope_s_folder_for_its_owner_alone_and_still_shown(tmp_path: Path) -> None:
    rig = rigged(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    saved = await use(rig, {"save_to": "evidence/login"}, folder=project)
    file = project / "evidence" / "login.jpg"
    assert said(saved) == f"1 frame · 402x874px\nsaved {file} · {len(JPEG)} bytes · 402x874px"
    assert file.read_bytes() == JPEG and private(file), "a name with no ending is a JPEG, as it came"
    image = {"type": "image", "data": base64.b64encode(JPEG).decode(), "mimeType": "image/jpeg"}
    assert saved["content"][1:] == [image] and saved["isError"] is False
    assert sorted(path.name for path in (project / "evidence").iterdir()) == ["login.jpg"], "no partial file is left"


async def test_return_image_false_answers_only_where_each_numbered_frame_was_saved(tmp_path: Path) -> None:
    rig = rigged(tmp_path)
    root = tmp_path / "client"
    root.mkdir()
    caller = dataclasses.replace(CALLER, roots=(root,))
    saved = await use(rig, {"save_to": "run.JPEG", "frames": 2, "return_image": False}, caller=caller)
    first, second = root / "run-1.JPEG", root / "run-2.JPEG"
    assert saved == text(
        f"2 frames · 402x874px\nsaved {first} · {len(JPEG)} bytes · 402x874px\n"
        f"saved {second} · {len(JPEG)} bytes · 402x874px"
    )
    assert first.read_bytes() == second.read_bytes() == JPEG and private(first) and private(second)
    assert not (root / "run.JPEG").exists()


async def test_a_png_is_the_same_picture_written_again_as_a_png(tmp_path: Path) -> None:
    rig = rigged(tmp_path)
    assert rig.idb is not None
    rig.idb.engine.shot = Shot(picture(402, 874, boxes=((10, 10, 50, 50, 0),)), 402, 874)
    file = tmp_path / "Movies" / "shots" / "settings.png"
    saved = await use(rig, {"save_to": str(file), "return_image": False})
    data = file.read_bytes()
    assert said(saved).endswith(f"saved {file} · {len(data)} bytes · 402x874px") and private(file)
    with Image.open(io.BytesIO(data)) as png:
        assert (png.format, png.size) == ("PNG", (402, 874)), "the recording folder takes a path, made as needed"


async def test_a_file_outside_the_folders_an_agent_may_write_to_is_refused_before_a_picture_is_taken(
    tmp_path: Path,
) -> None:
    rig = rigged(tmp_path)
    assert rig.idb is not None
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (project / "out").symlink_to(outside)
    (project / "shot.jpg").symlink_to(outside / "shot.jpg")
    allowed = f"{project}, {tmp_path / 'Movies'}"
    escapes = ("../elsewhere/shot.jpg", str(outside / "shot.jpg"), "out/shot.jpg", "shot.jpg", "shot", "~/shot.jpg")
    for save_to in (*escapes, "/"):
        refused = await use(rig, {"save_to": save_to}, folder=project)
        assert refused == text(f"save_to must be inside {allowed}: {save_to} is not", error=True), save_to
    assert not list(outside.iterdir()) and rig.idb.engine.screenshots == [], "nothing taken, nothing written"


async def test_what_save_to_and_return_image_may_be_is_checked_first(tmp_path: Path) -> None:
    rig = rigged(tmp_path)
    assert rig.idb is not None
    project = tmp_path / "project"
    (project / "shots").mkdir(parents=True)
    (project / "frames.jpg").mkdir()
    named = f"save_to is a file name or a path of at most {SAVE_TO_MAX} characters"
    for arguments, refusal in (
        ({"save_to": 3}, named),
        ({"save_to": " "}, named),
        ({"save_to": "a\x00.jpg"}, named),
        ({"save_to": "x" * (SAVE_TO_MAX + 1)}, named),
        ({"save_to": "shot.gif"}, "save_to ends in .jpg, .jpeg, .png -- a screenshot is saved as a JPEG or a PNG"),
        ({"save_to": "shots"}, f"save_to names a folder, {project / 'shots'}: name a file in it"),
        ({"save_to": "shots/x.jpg/.."}, f"save_to names a folder, {project / 'shots'}: name a file in it"),
        ({"save_to": "frames.jpg"}, f"save_to names a folder, {project / 'frames.jpg'}: name a file in it"),
        ({"return_image": "no"}, "return_image is true or false"),
        ({"return_image": False}, "return_image: false answers only where the picture was saved: give save_to too"),
    ):
        assert await use(rig, arguments, folder=project) == text(refusal, error=True), arguments
    assert rig.idb.engine.screenshots == []


async def test_another_checkout_of_the_launch_folder_s_repository_takes_a_screenshot_too(tmp_path: Path) -> None:
    rig = rigged(tmp_path)
    assert rig.idb is not None
    project = main_checkout(tmp_path / "app")
    worktree = linked_worktree(project, tmp_path / "app-wt")
    launched = project / "ios"
    launched.mkdir()
    # A worktree beside the launch folder, a folder of it not made yet, and the main checkout's other folders.
    for save_to, file in (
        (str(worktree / "evidence" / "login"), worktree / "evidence" / "login.jpg"),
        ("../docs/shot.jpg", project / "docs" / "shot.jpg"),
    ):
        saved = await use(rig, {"save_to": save_to, "return_image": False}, folder=launched)
        assert saved["isError"] is False and file.exists() and private(file), saved
    # From the worktree, the main checkout is another checkout of the same repository.
    saved = await use(rig, {"save_to": str(project / "main.jpg"), "return_image": False}, folder=worktree)
    assert saved["isError"] is False and (project / "main.jpg").read_bytes() == JPEG
    assert len(rig.idb.engine.screenshots) == 3


async def test_a_checkout_of_another_repository_or_none_or_git_s_own_folder_is_refused(tmp_path: Path) -> None:
    rig = rigged(tmp_path)
    assert rig.idb is not None
    project = main_checkout(tmp_path / "app")
    other = main_checkout(tmp_path / "other")
    lookalike = tmp_path / "copy"
    lookalike.mkdir()
    (lookalike / ".git").write_text(f"gitdir: {project / '.git' / 'worktrees' / 'copy'}\n")
    plain = tmp_path / "plain"
    plain.mkdir()
    launched = project / "ios"
    launched.mkdir()
    allowed = f"save_to must be inside {launched}, {tmp_path / 'Movies'} or another checkout of its repository"
    for save_to in (
        other / "shot.jpg",
        lookalike / "shot.jpg",
        plain / "shot.jpg",
        project / ".git" / "shot.jpg",
        project / ".git" / "worktrees" / "shot",
    ):
        refused = await use(rig, {"save_to": str(save_to)}, folder=launched)
        assert refused == text(f"{allowed}: {save_to} is not", error=True), save_to
    # A launch folder in no repository has no other checkout to name.
    refused = await use(rig, {"save_to": str(project / "shot.jpg")}, folder=plain)
    assert refused == text(
        f"save_to must be inside {plain}, {tmp_path / 'Movies'}: {project / 'shot.jpg'} is not", error=True
    )
    assert rig.idb.engine.screenshots == [] and not (other / "shot.jpg").exists()


async def test_a_file_that_cannot_be_written_says_why(tmp_path: Path) -> None:
    rig = rigged(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (project / "taken").write_text("a file, not a folder")
    refused = await use(rig, {"save_to": "taken/shot.jpg"}, folder=project)
    assert refused["isError"] is True and said(refused).startswith(f"could not save to {project / 'taken/shot.jpg'}: ")


async def test_a_screen_that_is_not_live_says_why_in_the_caption(tmp_path: Path) -> None:
    cable = (
        "Its cable could not show the screen (macOS has not let sim-mirror-helper use the Camera), so it shows "
        "screenshots instead."
    )
    phone = FakeConnector("phone", kinds=frozenset({"physical"}), session_capabilities=VIEW_ONLY, note=cable)
    rig = rigged(tmp_path, phones=FakePhoneBackend(), phone=phone)
    await rig.manager.choose(CALLER.scope, "00008120-0011223344556677")
    assert said(await use(rig, {})) == f"1 frame · 402x874px\nnote: {cable}"
    instance = await rig.manager.ensure(CALLER.scope)
    assert instance.session is not None
    instance.fallback_reason = None
    assert screen_note(instance) == cable, "the session's own note, when the device's says nothing"
    instance.capabilities |= {Capability.STREAM_H264}
    assert screen_note(instance) is None, "a live screen has nothing to explain"
    instance.capabilities -= {Capability.STREAM_H264}
    phone.helper_error = "iPhone has sent no picture of its screen in 15 seconds: is it unlocked?"
    assert screen_note(instance) == f"Its screen's helper said: {phone.helper_error}", "what the helper said since"
    instance.session = None
    assert screen_note(instance) is None


async def test_a_live_screen_s_caption_has_no_note(tmp_path: Path) -> None:
    rig = rigged(tmp_path)
    instance = await rig.manager.ensure(CALLER.scope)
    instance.fallback_reason = "something said earlier"
    assert said(await use(rig, {})) == "1 frame · 402x874px"
