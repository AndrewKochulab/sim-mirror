# SPDX-License-Identifier: Apache-2.0
"""`sim_screenshot`: a JPEG of the screen or a region of it, or several to see an animation -- shown to the agent,
saved to a file for evidence, or both.

A file is saved only inside a folder the agent may write to: the folder its client works in (its MCP roots), the
scope's own folder, or the recording folder (`recording.folder`). A file name or a relative path is taken in the first
of those; a path that leads out of them -- by ``..`` or by a link, followed first -- is refused. Each file is written
whole, for its owner alone (0600), and folders missing under an allowed one are made.

The screen arrives as a JPEG, so ``.jpg`` and ``.jpeg`` are saved as they came; ``.png`` is the same picture written
again as a PNG by Pillow, which SimMirror already decodes screenshots with -- lossless from then on, but no sharper
than the JPEG it was made from.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from sim_mirror.connectors.base import Capability, Shot
from sim_mirror.core.instance import DeviceInstance
from sim_mirror.core.recordings import default_folder
from sim_mirror.storage.private import write_atomic
from sim_mirror.tools.context import ToolContext, make_tool, ready_device
from sim_mirror.tools.results import Result, ToolRefused, images
from sim_mirror.tools.schemas import SAVE_TO_MAX

#: What each file ending is saved as; a name with none gets the first.
FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}
DEFAULT_SUFFIX = ".jpg"


async def run(args: dict[str, Any], ctx: ToolContext) -> Result:
    return_image = args.get("return_image", True)
    if not isinstance(return_image, bool):
        raise ToolRefused("return_image is true or false")
    save_to = destination(args.get("save_to"), places(ctx))
    if save_to is None and not return_image:
        raise ToolRefused("return_image: false answers only where the picture was saved: give save_to too")
    instance = await ready_device(ctx)
    shots = await ctx.actions.screenshots(
        instance,
        ctx.caller,
        width=args.get("width", ctx.config.screenshot_width),
        region=args.get("region"),
        frames=args.get("frames", 1),
        interval_ms=args.get("interval_ms"),
        max_elements=ctx.config.snapshot_max_elements,
    )
    lines = [f"{len(shots)} frame{'s' if len(shots) != 1 else ''} · {shots[0].width}x{shots[0].height}px"]
    if save_to is not None:
        try:
            lines += await asyncio.to_thread(save, shots, save_to)
        except OSError as exc:
            raise ToolRefused(f"could not save to {save_to}: {exc.strerror or exc}") from exc
    note = screen_note(instance)
    if note:
        lines.append(f"note: {note}")
    return images("\n".join(lines), [shot.jpeg for shot in shots] if return_image else [])


def screen_note(instance: DeviceInstance) -> str | None:
    """Why the device's screen is not live -- its cable's picture down, the Camera not allowed, screenshots taken
    instead -- as its connector says it; None while the screen streams, or when nothing is said."""
    if Capability.STREAM_H264 in instance.capabilities:
        return None
    return instance.live_note or (instance.session.note if instance.session is not None else None)


def places(ctx: ToolContext) -> tuple[Path, ...]:
    """Where a screenshot may be saved, the folder a relative path is taken in first: the scope's folder, the folders
    the agent's client works in, then the recording folder."""
    found = [*([ctx.folder] if ctx.folder is not None else []), *ctx.caller.roots, default_folder(ctx.config)]
    return tuple(dict.fromkeys(found))


def destination(value: object, allowed: Sequence[Path]) -> Path | None:
    """The file `save_to` names, with every link followed, inside one of the `allowed` folders; None for none."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > SAVE_TO_MAX or "\x00" in value:
        raise ToolRefused(f"save_to is a file name or a path of at most {SAVE_TO_MAX} characters")
    asked = Path(value).expanduser()
    real = _inside((asked if asked.is_absolute() else allowed[0] / asked).resolve(), allowed, value)
    if not real.suffix:
        if real.is_dir():
            raise ToolRefused(f"save_to names a folder, {real}: name a file in it")
        real = _inside(real.with_name(real.name + DEFAULT_SUFFIX).resolve(), allowed, value)
    if real.suffix.lower() not in FORMATS:
        raise ToolRefused(f"save_to ends in {', '.join(FORMATS)} -- a screenshot is saved as a JPEG or a PNG")
    if real.is_dir():
        raise ToolRefused(f"save_to names a folder, {real}: name a file in it")
    return real


def _inside(real: Path, allowed: Sequence[Path], value: str) -> Path:
    """`real` -- a path with every link followed -- when it is inside one of the `allowed` folders."""
    if not any(real.is_relative_to(place.resolve()) for place in allowed):
        raise ToolRefused(f"save_to must be inside {', '.join(str(place) for place in allowed)}: {value} is not")
    return real


def numbered(path: Path, count: int) -> list[Path]:
    """Where each of `count` pictures goes: the file itself for one, else ``name-1.jpg``, ``name-2.jpg`` ..."""
    if count == 1:
        return [path]
    return [path.with_name(f"{path.stem}-{index}{path.suffix}") for index in range(1, count + 1)]


def encoded(shot: Shot, path: Path) -> bytes:
    """The picture as its file's ending asks: the JPEG as it came, or written again as a PNG."""
    if FORMATS[path.suffix.lower()] == "JPEG":
        return shot.jpeg
    out = io.BytesIO()
    with Image.open(io.BytesIO(shot.jpeg)) as picture:
        picture.save(out, format="PNG")
    return out.getvalue()


def save(shots: Sequence[Shot], path: Path) -> list[str]:
    """Write each picture to its file, for its owner alone, making the folders it needs; a line saying each."""
    said = []
    for shot, file in zip(shots, numbered(path, len(shots)), strict=True):
        data = encoded(shot, file)
        file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_atomic(file, data, mode=0o600)
        said.append(f"saved {file} · {len(data)} bytes · {shot.width}x{shot.height}px")
    return said


TOOL = make_tool("sim_screenshot", (Capability.SCREENSHOT,), run)
