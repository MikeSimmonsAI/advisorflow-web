"""Memory probe for the backend's heavy background passes (P0 OOM investigation).

Runs a real background pass (the same function app.main schedules) in a worker
thread, exactly as `_off_loop` does in production, and samples the process RSS
every 20 ms. Prints RSS before / peak / after for each cycle so sustained growth
across repeated cycles is visible, not just one snapshot.

LOCAL / SYNTHETIC USE ONLY. It writes exception rows through the normal sweep
code path, so point DATABASE_URL at a disposable database, never production.

    DATABASE_URL=postgresql://...local... python scripts/memprobe_background_passes.py \
        --pass wholesale --org <org-id> --cycles 5
"""
import argparse
import gc
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def rss_mb() -> float:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    return 0.0


def run_measured(fn):
    peak = [rss_mb()]
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            peak[0] = max(peak[0], rss_mb())
            time.sleep(0.02)

    s = threading.Thread(target=sampler, daemon=True)
    s.start()
    before = rss_mb()
    t0 = time.monotonic()
    out = {}
    worker = threading.Thread(target=lambda: out.setdefault("r", fn()))
    worker.start()
    worker.join()
    secs = time.monotonic() - t0
    stop.set()
    s.join()
    return before, peak[0], rss_mb(), secs, out.get("r")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass", dest="which", choices=("wholesale", "evosense", "both", "overlap"), default="wholesale")
    ap.add_argument("--org", required=False)
    ap.add_argument("--cycles", type=int, default=5)
    ap.add_argument("--cap", type=int, default=None,
                    help="per-kind cap for the wholesale sweep (default: the production AUTO_CAP_PER_KIND); "
                         "a large value forces a full scan, the steady state once items are already open")
    args = ap.parse_args()

    import app.main  # noqa: F401  (the production import baseline)
    from app.deps import SessionLocal
    from app.services import wholesale_exceptions as wx
    from app.services.evosense import scheduler as evs

    def wholesale_pass():
        db = SessionLocal()
        try:
            made = wx.sweep(db, args.org, None, include_test=False, cap_per_kind=args.cap or wx.AUTO_CAP_PER_KIND)
            db.commit()
            return made
        finally:
            db.close()

    def evosense_pass():
        db = SessionLocal()
        try:
            return evs.run_due(db)
        finally:
            db.close()

    def finish(fn):
        # What the pass wrapper in app.main does after each heavy pass, when present.
        try:
            from app.services.memory_hygiene import after_heavy_pass
        except ImportError:
            return fn
        def wrapped():
            try:
                return fn()
            finally:
                after_heavy_pass("probe")
        return wrapped

    def overlap_pass():
        # The pre-fix production shape: hunt and sweep started together in two
        # worker threads (separate loops, no shared turn).
        out = {}
        ts = [threading.Thread(target=lambda n=n, f=f: out.setdefault(n, f()))
              for n, f in (("wholesale", wholesale_pass), ("evosense", evosense_pass))]
        [t.start() for t in ts]
        [t.join() for t in ts]
        return {"overlapped": sorted(out)}

    passes = {"wholesale": [wholesale_pass], "evosense": [evosense_pass],
              "both": [wholesale_pass, evosense_pass], "overlap": [overlap_pass]}[args.which]
    gc.collect()
    print("baseline after import: %.1f MB" % rss_mb())
    for c in range(1, args.cycles + 1):
        for fn in passes:
            b, p, a, secs, r = run_measured(finish(fn))
            short = {k: v for k, v in (r or {}).items() if v} if isinstance(r, dict) else r
            print("cycle %d %-15s before=%6.1f peak=%6.1f after=%6.1f MB  (+%5.1f peak, %+5.1f kept)  %.1fs  %s"
                  % (c, fn.__name__, b, p, a, p - b, a - b, secs, short))
    print("final: %.1f MB" % rss_mb())


if __name__ == "__main__":
    main()
