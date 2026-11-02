"""Give memory back to the OS after a heavy background pass (P0, 2026-10-29).

WHY THIS EXISTS
The production backend runs on a 512 MiB Render instance and idles at roughly
410-470 MiB (the app itself imports to ~300 MiB). The scheduled EvoSense hunt
and Wholesale exception sweep each load thousands of ORM rows in a worker
thread. When a pass finishes, Python frees those objects, but glibc keeps the
freed pages in its heap rather than returning them, so the process RSS - the
number Render's OOM killer looks at - stays at the pass's high-water mark.
Render metrics show exactly that: one-minute steps of +13 to +63 MiB at hunt /
sweep time that never come back down, until an allocation lands on top and
the instance is killed (eight OOM kills on Oct 28-29).

WHAT IT DOES
`after_heavy_pass()` runs a full garbage collection (ORM objects sit in
reference cycles with their session state, so they are not all freed by
reference counting) and then calls glibc's `malloc_trim(0)`, which returns
free heap pages to the OS. Both are standard, have no effect on program state,
and take milliseconds. On a non-glibc platform (macOS, Windows, musl) the trim
is skipped and only the collection runs.

WHAT IT DOES NOT DO
It does not make a pass allocate less - the bounded-batch changes in the
passes themselves do that. It lowers the floor the NEXT allocation starts
from. It never raises: a hygiene failure must not fail the job.
"""
import ctypes
import ctypes.util
import gc
import logging
import os
import threading

log = logging.getLogger(__name__)

_lock = threading.Lock()
_trim = None
_trim_loaded = False


def _load_trim():
    global _trim, _trim_loaded
    if _trim_loaded:
        return _trim
    _trim_loaded = True
    try:
        name = ctypes.util.find_library("c")
        if name:
            libc = ctypes.CDLL(name)
            fn = getattr(libc, "malloc_trim", None)
            if fn is not None:
                fn.argtypes = [ctypes.c_size_t]
                fn.restype = ctypes.c_int
                _trim = fn
    except Exception:                                   # noqa: BLE001
        _trim = None
    return _trim


def rss_bytes():
    """Current resident set size, or None where /proc is unavailable."""
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def after_heavy_pass(label: str = "") -> dict:
    """Collect garbage and return freed heap pages to the OS. Never raises.

    Returns {"rss_before_mb", "rss_after_mb", "trimmed"} for logging; the RSS
    values are None where they cannot be read."""
    out = {"rss_before_mb": None, "rss_after_mb": None, "trimmed": False}
    if os.environ.get("MEMORY_HYGIENE_DISABLED", "").strip().lower() in ("1", "true", "yes"):
        return out
    try:
        with _lock:
            before = rss_bytes()
            gc.collect()
            fn = _load_trim()
            if fn is not None:
                fn(0)
                out["trimmed"] = True
            after = rss_bytes()
        if before is not None:
            out["rss_before_mb"] = round(before / 1048576, 1)
        if after is not None:
            out["rss_after_mb"] = round(after / 1048576, 1)
        if before is not None and after is not None:
            log.info("memory_hygiene %s: rss %.1f -> %.1f MiB", label or "pass",
                     out["rss_before_mb"], out["rss_after_mb"])
    except Exception as exc:                            # noqa: BLE001
        log.warning("memory_hygiene %s skipped: %s", label or "pass", exc)
    return out
