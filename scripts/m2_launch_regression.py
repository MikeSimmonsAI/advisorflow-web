"""Launch the full backend regression so it OUTLIVES whoever started it.

Two earlier attempts died mid-suite. The first was a plain child of the shell
that launched it. The second used `start`, which is still inside the launching
console's process tree - so when the machine's bridge dropped, the tree went and
took two hours of test time with it.

DETACHED_PROCESS drops the console, CREATE_NEW_PROCESS_GROUP stops Ctrl-events
reaching it, and CREATE_BREAKAWAY_FROM_JOB escapes any Job object the parent
shell lives in - which is the one that actually kills these. Output goes to a
file for the same reason: the evidence has to outlive whoever was watching.

    python scripts/m2_launch_regression.py [out_path]
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = r"C:\Dev\advisorflow-web\.venv\Scripts\python.exe"
OUT = sys.argv[1] if len(sys.argv) > 1 else r"C:\Dev\_m2_full.txt"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
FLAGS = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB

if os.path.exists(OUT):
    os.remove(OUT)

# The command lives in a .bat rather than inline. `cmd /c` mangles a command
# line that both starts with a quote and carries redirection, so the inline
# version died instantly and left no output file - indistinguishable from a
# suite that never ran. A file on disk has no quoting rules to get wrong.
INNER = os.path.join(REPO, "scripts", "m2_regression_inner.bat")

for flags in (FLAGS, DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP):
    try:
        p = subprocess.Popen(["cmd.exe", "/c", INNER, OUT], cwd=REPO,
                             creationflags=flags, close_fds=True)
        print("detached regression pid %s (flags 0x%08x)" % (p.pid, flags))
        print("watch %s" % OUT)
        sys.exit(0)
    except OSError as e:                                     # noqa: PERF203
        # CREATE_BREAKAWAY_FROM_JOB fails if the job forbids breakaway; fall
        # back rather than silently running attached.
        print("launch with flags 0x%08x failed (%s); retrying" % (flags, e))

print("could not launch a detached regression")
sys.exit(1)
