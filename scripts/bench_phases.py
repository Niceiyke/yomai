"""Micro-benchmarks for Phase 1 performance fixes."""
import asyncio
import timeit
from textwrap import dedent

LLM_CHUNK_1000 = "chat " * 250  # ~1250 chars, no reasoning
LLM_CHUNK_REASONING = "<think>" + ("x" * 500) + "</think>" + "chat " * 250
LLM_CHUNK_SPLIT_START = "<think>" + ("x" * 500)
LLM_CHUNK_SPLIT_END = ("y" * 500) + "</think>" + "chat " * 250

# ── strip_reasoning benchmarks ──────────────────────────────────────────


def _strip_old(text: str, inside: list[bool]) -> str:
    """Old O(n^2) implementation using startswith per char."""
    output: list[str] = []
    i = 0
    while i < len(text):
        if not inside[0] and text.startswith("<think>", i):
            inside[0] = True
            i += len("<think>")
        elif inside[0] and text.startswith("</think>", i):
            inside[0] = False
            i += len("</think>")
        elif inside[0]:
            i += 1
        else:
            output.append(text[i])
            i += 1
    return "".join(output)


def _strip_new(text: str, inside: list[bool]) -> str:
    """New O(n) implementation using str.find."""
    if inside[0]:
        text = "<think>" + text
    output: list[str] = []
    pos = 0
    while pos < len(text):
        start = text.find("<think>", pos)
        if start == -1:
            output.append(text[pos:])
            break
        output.append(text[pos:start])
        end = text.find("</think>", start + 7)
        if end == -1:
            inside[0] = True
            break
        inside[0] = False
        pos = end + 8
    return "".join(output)


def bench_strip():
    print("── strip_reasoning ──")
    for label, chunks in [
        ("plain (1k)", [LLM_CHUNK_1000]),
        ("reasoning (1.5k)", [LLM_CHUNK_REASONING]),
        ("split (1k+1k)", [LLM_CHUNK_SPLIT_START, LLM_CHUNK_SPLIT_END]),
    ]:
        old_time = timeit.timeit(
            lambda: [_strip_old(c, [False]) for c in chunks], number=10_000
        )
        new_time = timeit.timeit(
            lambda: [_strip_new(c, [False]) for c in chunks], number=10_000
        )
        speedup = old_time / new_time
        print(f"  {label:20s}  old: {old_time*1000:7.2f}ms  new: {new_time*1000:7.2f}ms  {speedup:.1f}x")


# ── SSE helper overhead ─────────────────────────────────────────────────


def bench_sse():
    print("\n── SSE helper overhead (100 events) ──")

    async def _run_old(n: int):
        s = ""
        for _ in range(n):
            s += "event: chunk\ndata: {\"content\":\"x\"}\n\n"
        s += "event: done\ndata: {\"type\":\"done\"}\n\n"
        return s

    async def _main():
        import time
        n_calls = 100_000
        # old-style: simulate the await overhead via coroutine creation
        t0 = time.perf_counter()
        for _ in range(n_calls):
            await _run_old(100)  # simulates async overhead
        old_time = time.perf_counter() - t0

        def _run_new(n: int):
            s = ""
            for _ in range(n):
                s += "event: chunk\ndata: {\"content\":\"x\"}\n\n"
            s += "event: done\ndata: {\"type\":\"done\"}\n\n"
            return s

        t0 = time.perf_counter()
        for _ in range(n_calls):
            _run_new(100)
        new_time = time.perf_counter() - t0

        speedup = old_time / new_time
        print(f"  {'async (coroutine)':20s}  {old_time*1000:7.2f}ms ({n_calls} calls)")
        print(f"  {'sync (def)':20s}  {new_time*1000:7.2f}ms ({n_calls} calls)  {speedup:.1f}x")

    asyncio.run(_main())


if __name__ == "__main__":
    bench_strip()
    bench_sse()
