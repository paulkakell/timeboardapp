"""Compare in-process MCP/GPT Actions reads on the same disposable fixture.

This microbenchmark measures framework/authentication overhead, not production
network latency, write throughput, or multi-worker capacity.
"""

from __future__ import annotations

import json
import logging
import runpy
import statistics
import sys
import tempfile
import time
from pathlib import Path

import pytest
from starlette.responses import Response

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    # Keep protocol URLs out of benchmark output and avoid timing log I/O.
    logging.disable(logging.INFO)
    helpers = runpy.run_path(str(ROOT / "tests/test_mcp.py"))
    with (
        tempfile.TemporaryDirectory(prefix="timeboard-mcp-benchmark-") as raw,
        pytest.MonkeyPatch.context() as patch,
    ):
        fixture = helpers["system"].__wrapped__(Path(raw), patch)
        system = next(fixture)
        try:
            _, tokens = helpers["connect"](system, scopes=["tasks:read"])
            with system["sessions"]() as db:
                user = db.get(helpers["User"], system["ids"]["manager"])
                for index in range(100):
                    helpers["crud"].create_task(
                        db,
                        owner=user,
                        name=f"Synthetic task {index}",
                        task_type="benchmark",
                        due_date=None,
                        send_notifications=False,
                    )
                actions = (
                    helpers["chatgpt"]
                    .issue_token(
                        helpers["chatgpt"].TokenCreate(name="Synthetic benchmark"),
                        Response(),
                        db=db,
                        user=user,
                    )
                    .token
                )

            def rest():
                response = system["client"].get(
                    "/api/chatgpt/tasks?limit=10",
                    headers={"Authorization": "Bearer " + actions},
                )
                assert response.status_code == 200
                assert len(response.json()["items"]) == 10

            def mcp():
                result = helpers["value"](
                    helpers["call"](system, tokens, "list_tasks", {"limit": 10})
                )
                assert len(result["items"]) == 10

            results = {"tasks": 100, "page_size": 10, "samples": 30}
            for name, operation in [("actions", rest), ("mcp", mcp)]:
                for _ in range(5):
                    operation()
                timings = []
                for _ in range(30):
                    start = time.perf_counter()
                    operation()
                    timings.append((time.perf_counter() - start) * 1000)
                results[name] = {
                    "median_ms": round(statistics.median(timings), 2),
                    "p95_ms": round(sorted(timings)[28], 2),
                }
            print(json.dumps(results, indent=2))
        finally:
            fixture.close()


if __name__ == "__main__":
    main()
