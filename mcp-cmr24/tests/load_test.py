"""Deterministic in-process load test; no real CMR24 writes are performed."""

import argparse
import asyncio
import logging
import statistics
import time
import tracemalloc

from api_connector import set_api_client
from auth_context import caller_scopes_var, request_id_var
from tools import dispatch_operation


class FakeAPI:
    async def request(self, endpoint, **kwargs):
        await asyncio.sleep(0)
        return {"Cargo": []}


async def run(requests: int, concurrency: int) -> dict:
    logging.getLogger("cmr24.operations").setLevel(logging.WARNING)
    set_api_client(FakeAPI())
    scope = caller_scopes_var.set(frozenset({"cargo.read"}))
    request = request_id_var.set("load-test")
    semaphore = asyncio.Semaphore(concurrency)
    latencies = []
    errors = 0
    tracemalloc.start()

    async def one():
        nonlocal errors
        async with semaphore:
            started = time.perf_counter()
            result = await dispatch_operation(
                "get_cargo_list", {"type": 0, "status": 0}
            )
            latencies.append((time.perf_counter() - started) * 1000)
            errors += int(not result["ok"])

    started = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(requests)))
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    request_id_var.reset(request)
    caller_scopes_var.reset(scope)
    set_api_client(None)
    ordered = sorted(latencies)
    return {
        "requests": requests,
        "concurrency": concurrency,
        "errors": errors,
        "throughput_rps": round(requests / elapsed, 1),
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3),
        "peak_mib": round(peak / 1024 / 1024, 2),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=5000)
    parser.add_argument("--concurrency", type=int, default=100)
    args = parser.parse_args()
    result = asyncio.run(run(args.requests, args.concurrency))
    print(result)
    if result["errors"] or result["p95_ms"] > 250 or result["peak_mib"] > 64:
        raise SystemExit(1)
