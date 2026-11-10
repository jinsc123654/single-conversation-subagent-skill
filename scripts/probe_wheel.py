#!/usr/bin/env python3
"""Probe an isolated installed wheel with SDK 2.3 clients, modern and legacy."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile

from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def main(args):
    results = []
    for mode in ('auto', 'legacy'):
        with tempfile.TemporaryDirectory(prefix='scs-wheel-') as root:
            params = StdioServerParameters(command=sys.executable,
                args=['-m', 'scs_mcp', '--project-root', root], env={'PYTHONPATH':str(Path(args.installed).resolve())})
            async with Client(params, mode=mode, read_timeout_seconds=5) as client:
                async def call(name, **arguments):
                    value = (await client.call_tool(name, arguments)).structured_content
                    assert value and value.get('status') != 'ERROR', value
                    return value
                diagnostics = await call('diagnostics_check')
                assert diagnostics['server_version'] == '2.2.0'
                tools = await client.list_tools()
                assert len(tools.tools) == 17
                sid = (await call('session_open', project_root=root, client_name='wheel-probe', explicit_skip=True))['id']
                view = await call('project_view_refresh', session_id=sid, initialize_vault=True)
                assert view['view_initialized'] and view['local_config_present']
                info = await call('project_view_info', session_id=sid)
                assert not info['needs_refresh']
                results.append({'mode':mode,'protocol':diagnostics['protocol_version'], 'server':diagnostics['server_version'],
                    'tools':len(tools.tools), 'view':'PASS', 'result':'PASS'})
    Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(results))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--installed',required=True)
    parser.add_argument('--output',required=True)
    asyncio.run(main(parser.parse_args()))
