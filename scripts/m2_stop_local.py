"""Stop ONLY the uvicorn this run started, identified by its port.

Killing by window title was the first version and it was wrong: on a machine
with several worktrees running their own servers, a title match can end
somebody else's process. A command line containing `--port <this port>` and
`app.main:app` cannot.
"""
import os
import subprocess
import sys

port = sys.argv[1] if len(sys.argv) > 1 else "8137"
needle = "--port %s" % port

ps = ("Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
      "Where-Object { $_.CommandLine -like '*app.main:app*' -and "
      "$_.CommandLine -like '*" + needle + "*' } | "
      "Select-Object -ExpandProperty ProcessId")

try:
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, timeout=60).stdout
except Exception as e:                                       # noqa: BLE001
    print("could not enumerate processes: %s" % e)
    sys.exit(0)

pids = [line.strip() for line in out.splitlines() if line.strip().isdigit()]
if not pids:
    print("nothing to stop on port %s" % port)
    sys.exit(0)

for pid in pids:
    subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                   capture_output=True, text=True)
    print("stopped uvicorn pid %s (port %s)" % (pid, port))
