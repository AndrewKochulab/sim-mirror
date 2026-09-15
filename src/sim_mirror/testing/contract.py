# SPDX-License-Identifier: Apache-2.0
"""What every connector must do, as one check a connector's own tests can run.

    problems = await check_connector(MyConnector(...), SimConfig.defaults(), udid)
    assert problems == []

It probes, attaches to a device the connector's own fakes stand for, uses each role the reported capabilities
promise, and closes the session twice. Every broken promise is listed, so one run says all that is wrong.
"""

from __future__ import annotations

from sim_mirror.config.model import SimConfig
from sim_mirror.connectors.base import INPUT_CAPABILITIES, Capability, Connector, ConnectorReport, DeviceSession


def _report_problems(connector: Connector, report: ConnectorReport) -> list[str]:
    problems: list[str] = []
    if report.name != connector.name:
        problems.append(f"the report names {report.name!r}, not the connector's name {connector.name!r}")
    if not all(isinstance(capability, Capability) for capability in report.capabilities):
        problems.append("the report's capabilities are Capability members")
    if not report.available and not report.reasons:
        problems.append("a connector that cannot be used says why, in its report's reasons")
    if report.available and report.reasons:
        problems.append("a connector that can be used gives no reasons")
    return problems


async def _session_problems(session: DeviceSession, connector: Connector, report: ConnectorReport) -> list[str]:
    problems: list[str] = []
    if session.connector != connector.name:
        problems.append(f"the session names {session.connector!r}, not {connector.name!r}")
    if session.capabilities != report.capabilities:
        problems.append("the session has the capabilities the report promised")
    if session.capabilities & INPUT_CAPABILITIES and session.input is None:
        problems.append("a connector with input capabilities gives an InputSink")
    if Capability.ELEMENT_TREE in session.capabilities and session.reader is None:
        problems.append("a connector with element_tree gives a ScreenReader")
    if not session.alive:
        problems.append("a session just attached is alive")
    screen = await session.screen.describe()
    if min(screen.width_px, screen.height_px, screen.width_pt, screen.height_pt) <= 0 or screen.scale <= 0:
        problems.append("the screen has a size in pixels and in points, and a scale")
    if Capability.SCREENSHOT in session.capabilities:
        shot = await session.screen.screenshot(max_width=400, quality=70)
        if not shot.jpeg.startswith(b"\xff\xd8") or shot.width <= 0 or shot.height <= 0:
            problems.append("a screenshot is a JPEG with a size")
    if Capability.ELEMENT_TREE in session.capabilities and session.reader is not None:
        document: object = await session.reader.accessibility()
        if not isinstance(document, dict):
            problems.append("the accessibility document is an object")
    return problems


async def check_connector(connector: Connector, config: SimConfig, udid: str) -> list[str]:
    """Everything the connector does that a connector must not, for a device `udid` its fakes stand for."""
    report = await connector.probe(config)
    problems = _report_problems(connector, report)
    if not report.available:
        return problems
    session = await connector.attach(udid, config)
    try:
        problems += await _session_problems(session, connector, report)
    finally:
        await session.close()
        await session.close()
    if session.alive:
        problems.append("a closed session is not alive")
    reaped = await connector.reap_orphans()
    if not isinstance(reaped, int) or reaped < 0:
        problems.append("reap_orphans answers how many it ended")
    return problems
