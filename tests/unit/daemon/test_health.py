# SPDX-License-Identifier: Apache-2.0
"""A health check's proof: made only with the admin token, bound to its nonce, and checked in constant time."""

from __future__ import annotations

from sim_mirror.daemon.health import new_nonce, proof, proves


def test_a_proof_is_bound_to_its_token_and_its_nonce() -> None:
    made = proof("admin-token", "n1")
    assert made == proof("admin-token", "n1") and len(made) == 64
    assert made != proof("admin-token", "n2") and made != proof("other-token", "n1")


def test_only_the_right_proof_proves() -> None:
    assert proves("admin-token", "n1", proof("admin-token", "n1"))
    assert not proves("admin-token", "n1", proof("admin-token", "n2"))
    assert not proves("admin-token", "n1", "forged") and not proves("admin-token", "n1", None)


def test_nonces_are_fresh() -> None:
    first, second = new_nonce(), new_nonce()
    assert first != second and len(first) == 32 and int(first, 16) >= 0
