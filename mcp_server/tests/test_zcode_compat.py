"""The ZCode compatibility filter keeps legacy and modern clients separated."""

import anyio
import mcp_types as types
from mcp.shared.message import SessionMessage

from scs_mcp.zcode_compat import zcode_compatible_stream


def meta_for(name):
    return {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": name, "version": "1"},
    }


def request(ident, method, params=None):
    return types.JSONRPCRequest(jsonrpc="2.0", id=ident, method=method, params=params)


def notification(method, params=None):
    return types.JSONRPCNotification(jsonrpc="2.0", method=method, params=params)


async def run_filter(frames):
    read_send, read_receive = anyio.create_memory_object_stream(10)
    write_send, write_receive = anyio.create_memory_object_stream(10)
    for frame in frames:
        await read_send.send(SessionMessage(message=frame))
    await read_send.aclose()

    forwarded = []
    async for item in zcode_compatible_stream(read_receive, write_send):
        forwarded.append(item.message)
    await write_send.aclose()

    replies = []
    async for item in write_receive:
        replies.append(item.message)
    return forwarded, replies


def test_zcode_probe_falls_back_and_envelopes_are_removed():
    initialize = request(
        2,
        "initialize",
        {
            "protocolVersion": "2026-07-28",
            "clientInfo": {"name": "zcode", "version": "1"},
            "capabilities": {},
            "_meta": meta_for("zcode"),
        },
    )
    tools = request(3, "tools/list", {"_meta": meta_for("zcode")})
    forwarded, replies = anyio.run(
        run_filter,
        [
            request(1, "server/discover", {"_meta": meta_for("zcode")}),
            initialize,
            notification("notifications/initialized"),
            tools,
        ],
    )

    assert len(replies) == 1
    assert replies[0].error.code == types.UNSUPPORTED_PROTOCOL_VERSION
    assert replies[0].error.data == {"supported": ["2025-11-25"], "requested": "2026-07-28"}

    assert [message.method for message in forwarded] == ["initialize", "notifications/initialized", "tools/list"]
    assert "_meta" not in forwarded[0].params
    assert forwarded[0].params["protocolVersion"] == "2026-07-28"
    assert "_meta" not in forwarded[2].params


def test_identified_modern_client_is_untouched():
    discover = request(1, "server/discover", {"_meta": meta_for("reference-client")})
    tools = request(2, "tools/list", {"_meta": meta_for("reference-client")})
    forwarded, replies = anyio.run(run_filter, [discover, tools])

    assert replies == []
    assert [message.method for message in forwarded] == ["server/discover", "tools/list"]
    assert forwarded[0].params["_meta"] == meta_for("reference-client")
    assert forwarded[1].params["_meta"] == meta_for("reference-client")


def test_legacy_connection_drops_a_late_envelope():
    initialize = request(
        1,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "clientInfo": {"name": "legacy-client", "version": "1"},
            "capabilities": {},
        },
    )
    tools = request(2, "tools/list", {"_meta": meta_for("legacy-client")})
    forwarded, replies = anyio.run(run_filter, [initialize, tools])

    assert replies == []
    assert [message.method for message in forwarded] == ["initialize", "tools/list"]
    assert "_meta" not in forwarded[1].params
