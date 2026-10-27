# SPDX-License-Identifier: Apache-2.0
"""Tickets, tokens and codes never reach the log, however uvicorn formats the line."""

from __future__ import annotations

import logging

from sim_mirror.cli.context import daemon_log_config
from sim_mirror.server.log_redaction import LEASE_PATH, LOGGERS, CredentialFilter, LeaseLineFilter, install, redact


def test_every_credential_in_a_url_is_replaced_and_everything_else_is_kept() -> None:
    line = 'GET /api/v1/scopes/tp-1/screen?ticket=abc123&x=1 then /viewer/tp-1#code=zz&token=t0k "quoted?token=q"'
    assert (
        redact(line) == 'GET /api/v1/scopes/tp-1/screen?ticket=…&x=1 then /viewer/tp-1#code=…&token=… "quoted?token=…"'
    )
    assert redact("/healthz") == "/healthz"


def test_a_record_is_redacted_in_its_message_and_its_arguments_and_never_dropped() -> None:
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "%s %s?ticket=secret", ("GET", 200), None)
    record.args = ('127.0.0.1 - "GET /screen?ticket=secret HTTP/1.1"', 101)
    assert CredentialFilter().filter(record) is True
    assert record.msg == "%s %s?ticket=…" and record.args == ('127.0.0.1 - "GET /screen?ticket=… HTTP/1.1"', 101)
    mapped = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, {"not": "text"}, None, None)
    assert CredentialFilter().filter(mapped) is True and mapped.msg == {"not": "text"}


def test_installing_twice_filters_each_line_once() -> None:
    install()
    install()
    for name in LOGGERS:
        filters = [existing for existing in logging.getLogger(name).filters if isinstance(existing, CredentialFilter)]
        assert len(filters) == 1


def access(path: str, status: int) -> logging.LogRecord:
    """A request line as uvicorn's access log writes it."""
    message = '%s - "%s %s HTTP/%s" %d'
    args = ("127.0.0.1:5000", "POST", path, "1.1", status)
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, message, args, None)


def test_a_lease_renewed_is_left_out_of_the_access_log_and_every_other_line_is_kept() -> None:
    lines = LeaseLineFilter()
    assert lines.filter(access(LEASE_PATH, 200)) is False
    assert lines.filter(access(LEASE_PATH + "?x=1", 200)) is False
    assert lines.filter(access(LEASE_PATH, 401)) is True, "a refused lease is worth seeing"
    assert lines.filter(access("/api/v1/agent/call", 200)) is True
    assert lines.filter(logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "odd", None, None)) is True
    install()
    install()
    access_filters = logging.getLogger("uvicorn.access").filters
    assert len([kept for kept in access_filters if isinstance(kept, LeaseLineFilter)]) == 1
    assert not any(isinstance(kept, LeaseLineFilter) for kept in logging.getLogger("uvicorn.error").filters)


def test_every_line_the_daemon_writes_says_when() -> None:
    config = daemon_log_config()
    assert all(formatter["fmt"].startswith("%(asctime)s ") for formatter in config["formatters"].values())
    assert config["loggers"]["sim_mirror"] == {"handlers": ["default"], "level": "INFO"}
