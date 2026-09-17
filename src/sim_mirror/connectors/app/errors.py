# SPDX-License-Identifier: Apache-2.0
"""Why an app's hierarchy could not be read. Each is a `ConnectorError`, so a snapshot says it as a note and goes on."""

from __future__ import annotations

from sim_mirror.connectors.base import ConnectorError


class AppSdkError(ConnectorError):
    """The app answered, but not with a hierarchy SimMirror can read."""


class AppUnreachable(AppSdkError):
    """Nothing listens where the app said, or it hung up before answering: the app has gone."""


class AppTimedOut(AppSdkError):
    """The app did not answer in time: it is busy, paused in a debugger, or suspended in the background."""


class AppInactive(AppSdkError):
    """The app is not in front, so it has nothing on screen to say."""


class AppRefused(AppSdkError):
    """The app refused the request, with an HTTP status."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status
