# SPDX-License-Identifier: Apache-2.0
"""Credentials kept out of the server's log.

A browser cannot put a header on a WebSocket, so a viewer spends a one-shot ticket in its screen socket's URL, and a
token can arrive in a query string too. Uvicorn logs every request line and every accepted socket with its query
string, which would put each one in the log in full. A ticket is spent by the time the line is written, but a
credential still does not belong in a log.

`install` adds `CredentialFilter` to the two loggers uvicorn writes those lines through. It redacts the record's
arguments in place: uvicorn's access formatter unpacks them, so the message cannot simply be flattened.
"""

from __future__ import annotations

import logging
import re

#: The loggers uvicorn writes request lines (`uvicorn.access`) and accepted sockets (`uvicorn.error`) through.
LOGGERS = ("uvicorn.access", "uvicorn.error")

_CREDENTIAL = re.compile(r"([?&#](?:ticket|token|code)=)[^&\s\"']+")


def redact(text: str) -> str:
    """The text with every ``ticket=``, ``token=`` and ``code=`` value replaced by an ellipsis."""
    return _CREDENTIAL.sub(r"\1…", text)


class CredentialFilter(logging.Filter):
    """Redacts credentials from a record's message and arguments; never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(arg) if isinstance(arg, str) else arg for arg in record.args)
        return True


def install() -> None:
    """Keep credentials out of uvicorn's log lines, once however often it is called."""
    for name in LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(existing, CredentialFilter) for existing in logger.filters):
            logger.addFilter(CredentialFilter())
