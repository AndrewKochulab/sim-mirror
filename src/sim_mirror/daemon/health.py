# SPDX-License-Identifier: Apache-2.0
"""Telling this user's daemon from whatever else answers on its port, without giving anything a credential.

A port on 127.0.0.1 is anyone's to listen on: another account on the Mac, or a program started before the daemon. So
before the CLI sends the admin token or an agent token anywhere, it sends ``/healthz`` a fresh nonce and no credential,
and believes a listener is its daemon only when the answer carries `proof` of that nonce: an HMAC keyed by the admin
token, which only something able to read the admin token file can make. A new nonce each time means an answer
cannot be replayed.

A host holds no admin token, only its own. So it also names its token's id, and the answer carries a proof keyed by
that token's digest (`token_proof`): the daemon keeps the digest, the host can work it out from its token, and
anything else on the port has neither.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

#: Binds a proof to this use, so it can never double as any other MAC made with the admin token.
CONTEXT = b"sim-mirror/healthz:"
NONCE_MAX = 128


def new_nonce() -> str:
    return secrets.token_hex(16)


def proof(admin_token: str, nonce: str) -> str:
    """What only a holder of the admin token can answer to `nonce`."""
    return hmac.new(admin_token.encode("utf-8"), CONTEXT + nonce.encode("utf-8"), hashlib.sha256).hexdigest()


def proves(admin_token: str, nonce: str, answered: object) -> bool:
    """Whether `answered` is the proof for `nonce`, compared in constant time."""
    return isinstance(answered, str) and hmac.compare_digest(answered, proof(admin_token, nonce))


def token_proof(token_digest: str, nonce: str) -> str:
    """What only a holder of a scoped token's digest -- the daemon, or the token's own holder -- answers to `nonce`."""
    return proof(token_digest, nonce)


def token_proves(token: str, nonce: str, answered: object) -> bool:
    """Whether `answered` is the proof for `nonce` keyed by this token's digest."""
    return proves(hashlib.sha256(token.encode("utf-8")).hexdigest(), nonce, answered)
