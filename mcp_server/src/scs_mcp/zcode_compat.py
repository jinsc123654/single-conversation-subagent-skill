"""Stdio compatibility for hosts that probe modern and then speak legacy.

ZCode's stdio client sends ``server/discover`` with the 2026-07-28 envelope and
then continues with legacy-shaped requests. The SDK's dual-era loop settles the
connection era from the first request, so that mixed sequence locks the
connection to the modern era and the later requests are rejected.

This filter keeps the SDK behavior for identified non-ZCode clients and turns
the ZCode/unknown-discovery case into a normal 2025-11-25 handshake connection:
the probe is answered locally as a handshake-era server and the reserved
2026-07-28 ``_meta`` keys are removed before the SDK sees each message.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import mcp_types as types
from mcp.shared.message import SessionMessage

CLIENT_INFO_KEY = "io.modelcontextprotocol/clientInfo"
ENVELOPE_KEYS = (
    "io.modelcontextprotocol/protocolVersion",
    "io.modelcontextprotocol/clientCapabilities",
    CLIENT_INFO_KEY,
    "io.modelcontextprotocol/logLevel",
)
LEGACY_VERSION = "2025-11-25"


def _client_name(params: dict[str, Any] | None) -> str | None:
    """Read the client identity from a modern envelope or a legacy initialize."""
    if not isinstance(params, dict):
        return None
    meta = params.get("_meta")
    if isinstance(meta, dict):
        info = meta.get(CLIENT_INFO_KEY)
        if isinstance(info, dict) and isinstance(info.get("name"), str):
            return info["name"]
    info = params.get("clientInfo")
    if isinstance(info, dict) and isinstance(info.get("name"), str):
        return info["name"]
    return None


def _requested_version(params: dict[str, Any] | None) -> str:
    if isinstance(params, dict):
        meta = params.get("_meta")
        if isinstance(meta, dict):
            value = meta.get(ENVELOPE_KEYS[0])
            if isinstance(value, str):
                return value
        value = params.get("protocolVersion")
        if isinstance(value, str):
            return value
    return "2026-07-28"


def _strip_envelope(params: dict[str, Any] | None) -> None:
    """Remove only the spec-reserved modern keys; keep progressToken etc."""
    if not isinstance(params, dict):
        return
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return
    for key in ENVELOPE_KEYS:
        meta.pop(key, None)
    if not meta:
        params.pop("_meta", None)


def _legacy_only_error(request_id: Any, requested: str) -> types.JSONRPCError:
    return types.JSONRPCError(
        jsonrpc="2.0",
        id=request_id,
        error=types.ErrorData(
            code=types.UNSUPPORTED_PROTOCOL_VERSION,
            message=f"this server serves the {LEGACY_VERSION} handshake era; use initialize",
            data={"supported": [LEGACY_VERSION], "requested": requested},
        ),
    )


async def zcode_compatible_stream(read_stream, write_stream) -> AsyncIterator[Any]:
    """Filter a stdio read stream for ZCode's mixed-era handshake.

    The first request decides the mode:

    - ``server/discover`` from ZCode, or without a client identity: answer
      locally with -32022 and serve the rest as the legacy handshake era.
    - ``server/discover`` from an identified non-ZCode client: pass through and
      leave the SDK's modern era untouched.
    - anything else: the SDK already chooses the legacy era; strip the modern
      envelope from later messages so an enveloped initialize/request cannot
      be rejected by the legacy loop.
    """
    modern = False
    decided = False
    async for item in read_stream:
        if isinstance(item, Exception):
            yield item
            continue
        message = item.message
        method = getattr(message, "method", None)
        params = getattr(message, "params", None)
        if not decided:
            decided = True
            if method == "server/discover":
                name = _client_name(params)
                if name is not None and name.lower() != "zcode":
                    modern = True
                    yield item
                    continue
                if getattr(message, "id", None) is not None:
                    with suppress(Exception):
                        await write_stream.send(
                            SessionMessage(message=_legacy_only_error(message.id, _requested_version(params)))
                        )
                continue
        if modern:
            yield item
            continue
        if method == "server/discover":
            if getattr(message, "id", None) is not None:
                with suppress(Exception):
                    await write_stream.send(
                        SessionMessage(message=_legacy_only_error(message.id, _requested_version(params)))
                    )
            continue
        _strip_envelope(params)
        yield item
