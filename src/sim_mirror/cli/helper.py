# SPDX-License-Identifier: Apache-2.0
"""``sim-mirror helper``: the native helper this install runs.

* ``status`` says which helper the native connector would run -- the configured one, the one shipped in the wheel, or
  the one built for this version -- whether this SimMirror can use it, and where its Swift sources are;
* ``build`` builds it from those sources with the scope's Xcode, for this Mac, into SimMirror's state
  folder, where the native connector finds it. A release's wheel ships a built helper, so this is for a source
  checkout, an sdist, or a helper built again after Xcode changed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from typing import Any

from sim_mirror._version import __version__
from sim_mirror.cli.context import CliContext
from sim_mirror.connectors.native import wire
from sim_mirror.connectors.native.helper import (
    PACKAGED,
    PROGRAM,
    HelperVersion,
    built_helper,
    helper_version,
    install_helper,
    locate_helper,
)
from sim_mirror.host_copy import HostCopy
from sim_mirror.platform.local_signing import LocalSigning, SigningError
from sim_mirror.platform.swiftpm import build_package
from sim_mirror.storage.app_support import helpers_dir, installed_helper_dir, signing_dir
from sim_mirror.storage.private import ensure_private_dir


def register(commands: Any) -> None:
    command = commands.add_parser("helper", help="build the native helper, or say which one is used")
    actions = command.add_subparsers(dest="action", metavar="ACTION")
    status = actions.add_parser("status", help="say which native helper is used, and whether it can be")
    status.add_argument("--json", action="store_true", help="print it as JSON")
    status.add_argument("--scope", help="for this scope's settings, instead of this folder's project's")
    build = actions.add_parser("build", help="build the native helper with Xcode for this version of SimMirror")
    build.add_argument("--force", action="store_true", help="build it even when a usable one is already built")
    build.add_argument("--scope", help="build with this scope's Xcode, instead of this folder's project's")
    actions.add_parser(
        "sign",
        help="make this Mac's signing certificate for the helper, once, so Screen Recording and Accessibility "
        "granted to it are kept across upgrades (macOS asks for your password to trust it)",
    )
    command.set_defaults(handler=run, action="status", json=False, force=False, scope=None, signing=None)


def run(args: argparse.Namespace, ctx: CliContext) -> int:
    if args.action == "sign":
        return _sign(args, ctx)
    return asyncio.run(_build(args, ctx) if args.action == "build" else _status(args, ctx))


def _signing(args: argparse.Namespace, ctx: CliContext) -> LocalSigning:
    made: LocalSigning | None = getattr(args, "signing", None)
    return made if made is not None else LocalSigning(signing_dir(ctx.env, ctx.home), env=ctx.env)


def _sign(args: argparse.Namespace, ctx: CliContext) -> int:
    signing = _signing(args, ctx)
    ctx.say("making this Mac's helper-signing certificate; macOS asks for your password once to trust it")
    try:
        found = signing.set_up()
    except SigningError as exc:
        ctx.complain(f"sim-mirror: {exc}")
        return 1
    place = installed_helper_dir(ctx.env, ctx.home) / PROGRAM
    built = built_helper(helpers_dir(ctx.env, ctx.home))
    installed = install_helper((PACKAGED, built), place, signing)
    ctx.say(f"signing with {found}")
    if installed is None:
        ctx.say("no helper to install yet; the next one is signed with it")
        return 0
    ctx.say(
        f"installed {installed}, signed with it. Allow it once in System Settings > Privacy & Security > Screen "
        "Recording and > Accessibility (remove an earlier entry for it first); later versions keep that."
    )
    return 0


def _wanted() -> str:
    return f"version {__version__} (wire {wire.VERSION})"


async def _status(args: argparse.Namespace, ctx: CliContext) -> int:
    config = ctx.config().get(ctx.scope(args.scope))
    built = built_helper(helpers_dir(ctx.env, ctx.home))
    place = installed_helper_dir(ctx.env, ctx.home) / PROGRAM
    signing = _signing(args, ctx)
    installed = install_helper((PACKAGED, built), place, signing)

    async def version_of(binary: str) -> HelperVersion | None:
        return await helper_version(binary, ctx.run)

    shipped = (PACKAGED, built)
    found = await locate_helper(config.native_helper_path, (installed, *shipped) if installed else shipped, version_of)
    binary, version, usable = found.binary, found.version, found.usable
    reason = found.reason(HostCopy(), config.native_helper_path)
    sources = ctx.helper_sources()
    if args.json:
        ctx.say(json.dumps({
            "path": binary,
            "version": version.version if version else None,
            "wire": version.wire if version else None,
            "core_simulator": version.core_simulator if version else None,
            "usable": usable,
            "reason": reason,
            "built": str(built),
            "installed": str(place),
            "signed_by": signing.signer() or None,
            "sources": str(sources) if sources else None,
        }, indent=2))  # fmt: skip
    else:
        described = ""
        if version is not None:
            described = f" ({version.version}, wire {version.wire}, CoreSimulator {version.core_simulator})"
        ctx.say(f"native helper: {binary or 'none'}{described}")
        ctx.say("usable: yes" if usable else f"usable: no -- {reason}")
        ctx.say(f"built for this version: {built}" + ("" if built.is_file() else " (not built)"))
        allow = " (allow this one in Privacy & Security)" if installed else " (not installed)"
        ctx.say(f"installed at: {place}{allow}")
        signer = signing.signer()
        ctx.say(
            f"signed by: {signer}"
            if signer
            else "signed by: as shipped -- run `sim-mirror helper sign` once so a permission granted to it is kept "
            "across upgrades"
        )
        ctx.say(f"Swift sources: {sources or 'not in this install'}")
    return 0 if usable else 1


async def _build(args: argparse.Namespace, ctx: CliContext) -> int:
    config = ctx.config().get(ctx.scope(args.scope))
    helpers = helpers_dir(ctx.env, ctx.home)
    target = built_helper(helpers)
    if not args.force and target.is_file():
        version = await helper_version(str(target), ctx.run)
        if version is not None and version.usable:
            ctx.say(f"the native helper for this version is already built at {target} (--force builds it again)")
            return 0
    sources = ctx.helper_sources()
    if sources is None:
        ctx.complain("sim-mirror: this install has no Swift sources for the native helper to build")
        return 1
    xcode = config.developer_dir or "the Xcode xcode-select names"
    ctx.say(f"building the native helper from {sources} with {xcode}; a first build takes a minute or two")
    scratch = helpers / "native-build"
    built = await build_package(sources, scratch, developer_dir=config.developer_dir, xcrun=ctx.xcrun)
    if built.products is None:
        ctx.complain(f"sim-mirror: the native helper did not build: {built.failure}")
        return 1
    product = built.products / PROGRAM
    if not product.is_file():
        ctx.complain(f"sim-mirror: the build finished without a {PROGRAM} in {built.products}")
        return 1
    ensure_private_dir(target.parent)
    shutil.copy2(product, target)
    target.chmod(0o755)
    version = await helper_version(str(target), ctx.run)
    if version is None or not version.usable:
        said = "nothing readable" if version is None else f"version {version.version} (wire {version.wire})"
        ctx.complain(f"sim-mirror: the helper built at {target} says {said}, not {_wanted()}")
        return 1
    ctx.say(f"built the native helper at {target} (CoreSimulator {version.core_simulator or 'unknown'})")
    return 0
