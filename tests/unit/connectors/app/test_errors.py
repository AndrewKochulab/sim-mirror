# SPDX-License-Identifier: Apache-2.0
"""Why an app's hierarchy could not be read: each a connector error, so a snapshot goes on without it."""

from __future__ import annotations

from sim_mirror.connectors.app.errors import AppInactive, AppRefused, AppSdkError, AppTimedOut, AppUnreachable
from sim_mirror.connectors.base import ConnectorError


def test_every_reason_is_a_connector_error_and_a_refusal_keeps_its_status() -> None:
    for error in (AppSdkError("x"), AppUnreachable("x"), AppTimedOut("x"), AppInactive("x")):
        assert isinstance(error, ConnectorError)
    refused = AppRefused("the app refused the request (401)", 401)
    assert isinstance(refused, AppSdkError) and refused.status == 401 and str(refused).endswith("(401)")
