"""Re-record the HTTP fixtures from the live sites.

    python tests/record_fixtures.py

Run it from a network NSE does not block (an Indian connection works), then update RECORDED_AT in
scenarios.py to the time of the run and fix any assertions in test_tools.py that depend on the data.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cassette import FIXTURES, RecordingTransport  # noqa: E402
from mcp import Client  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402

from india_market_mcp import server  # noqa: E402


async def main() -> None:
    if FIXTURES.exists():
        shutil.rmtree(FIXTURES)
    transport = RecordingTransport()
    server.service_options.update(transport=transport)
    async with Client(server.mcp) as client:
        for name, args in SCENARIOS:
            result = await client.call_tool(name, args)
            status = "error" if result.is_error else "ok"
            print(f"{status:5} {name} {args}")
    print(f"Saved {len(set(transport.saved))} responses to {FIXTURES}")


if __name__ == "__main__":
    asyncio.run(main())
