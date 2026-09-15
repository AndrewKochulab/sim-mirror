# SPDX-License-Identifier: Apache-2.0
"""One-shot codes and the viewer sessions they buy, agents' leases, and what a standalone scope may do."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sim_mirror.build.xcodebuild import DERIVED_DATA
from sim_mirror.daemon.lease import LEASE_S, Leases
from sim_mirror.daemon.passes import CODE_TTL_S, VIEWER_TTL_S, OneShotCodes, ViewerSessions
from sim_mirror.daemon.policy import ConfigPolicy
from sim_mirror.daemon.tokens import TokenStore
from sim_mirror.scope import Scope
from sim_mirror.testing.fakes import ManualClock, MemoryStateStore, StaticConfig


def test_a_code_opens_its_scope_once_and_only_within_its_minute() -> None:
    clock = ManualClock()
    codes = OneShotCodes(clock=clock)
    first = codes.mint("tp-1")
    assert codes.redeem(first) == "tp-1" and codes.redeem(first) is None
    late = codes.mint("tp-1")
    clock.advance(CODE_TTL_S)
    assert codes.redeem(late) is None and codes.redeem("never-made") is None
    kept = codes.mint("tp-2")
    clock.advance(CODE_TTL_S / 2)
    codes.mint("tp-3")
    assert codes.redeem(kept) == "tp-2"


def test_a_viewer_token_is_for_its_scope_until_it_expires() -> None:
    clock = ManualClock()
    viewers = ViewerSessions(clock=clock)
    token = viewers.open("tp-1")
    assert viewers.scope_of(token) == "tp-1" and viewers.scope_of(None) is None and viewers.scope_of("forged") is None
    clock.advance(VIEWER_TTL_S)
    fresh = viewers.open("tp-2")
    assert viewers.scope_of(token) is None and viewers.scope_of(fresh) == "tp-2"


@dataclass
class Held:
    udid: str = "U"
    group: str = "local"
    scopes: tuple[str, ...] = ("tp-1",)


def test_a_lease_holds_a_scopes_device_until_it_runs_out_or_every_holder_lets_go() -> None:
    clock = ManualClock()
    leases = Leases(clock=clock)
    assert not leases.in_use(Held())
    assert leases.renew("tp-1", "t1") == clock.now + LEASE_S
    assert leases.held("tp-1") and leases.in_use(Held(scopes=("tp-9", "tp-1"))) and not leases.held("tp-9")
    clock.advance(LEASE_S - 1)
    leases.renew("tp-1", "t1")
    clock.advance(LEASE_S - 1)
    assert leases.held("tp-1")
    clock.advance(2)
    assert not leases.held("tp-1")
    leases.renew("tp-1", "t1")
    leases.renew("tp-1", "t2")
    leases.release("tp-1", "t1")
    assert leases.held("tp-1")
    leases.release("tp-1", "t2")
    leases.release("tp-1", "nobody")
    assert not leases.held("tp-1")


def test_a_standalone_scope_installs_from_its_tokens_folders_and_runs_commands_while_build_tools_are_on(
    tmp_path: Path,
) -> None:
    config = StaticConfig()
    tokens = TokenStore(tmp_path / "secrets")
    state = MemoryStateStore(tmp_path)
    policy = ConfigPolicy(config, tokens, state)
    demo = Scope.named("demo")
    assert policy.area_enabled(demo) and not policy.shells_allowed(demo) and policy.build_folder(demo) is None
    assert policy.install_roots(demo) == (state.derived_data(demo), DERIVED_DATA)
    tokens.create("agent", ["demo"], roots=["/Users/me/Notes", "/Users/me/Kit"])
    config.set(build_tools=True)
    assert policy.shells_allowed(demo) and policy.build_folder(demo) == Path("/Users/me/Notes")
    assert policy.install_roots(demo) == (
        Path("/Users/me/Notes"),
        Path("/Users/me/Kit"),
        state.derived_data(demo),
        DERIVED_DATA,
    )
