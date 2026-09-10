"""Serve the built frontend AND proxy the API on ONE origin, for the button audit.

TWO PROBLEMS THIS SOLVES, BOTH ARTEFACTS OF THE RIG RATHER THAN THE PRODUCT

1. SPA DEEP LINKS. Render's static service rewrites unknown paths to
   /index.html so /god/access/<id> survives a hard refresh. `http.server` does
   not, which would 404 and read as "the screen is broken".

2. ONE ORIGIN. The browser used for the audit will not make a cross-origin
   request to a second localhost port, so a frontend on :5173 talking to an API
   on :8011 fails at every call with "Failed to fetch" — again, nothing to do
   with the code under test. Proxying the API through the same port removes the
   variable entirely.

Order: static file, else proxy to the backend, else (a GET the backend does not
know) the SPA's index.html. Development rig only; never deployed.
"""
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "frontend", "dist")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5173
API = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8011"

STATIC_PREFIXES = ("/assets/", "/favicon", "/vite.svg", "/manifest")
HOP = {"host", "connection", "content-length", "accept-encoding"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ── static ──────────────────────────────────────────────────────────────
    def _static_path(self):
        path = self.path.split("?")[0]
        if path == "/":
            return os.path.join(ROOT, "index.html")
        if path.startswith(STATIC_PREFIXES) or "." in os.path.basename(path):
            candidate = os.path.normpath(os.path.join(ROOT, path.lstrip("/")))
            if candidate.startswith(ROOT) and os.path.isfile(candidate):
                return candidate
        return None

    def _send_file(self, path):
        with open(path, "rb") as fh:
            body = fh.read()
        ctype = ("text/html" if path.endswith(".html")
                 else "text/css" if path.endswith(".css")
                 else "application/javascript" if path.endswith(".js")
                 else "image/svg+xml" if path.endswith(".svg")
                 else "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ── proxy ───────────────────────────────────────────────────────────────
    def _proxy(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(API + self.path, data=body,
                                     method=self.command)
        for k, v in self.headers.items():
            if k.lower() not in HOP:
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, r.read(), r.headers.get("Content-Type",
                                                         "application/json")
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers.get("Content-Type",
                                                   "application/json")
        except Exception as e:                              # backend down
            return 502, str(e).encode(), "text/plain"

    def _handle(self):
        static = self._static_path()
        if static:
            return self._send_file(static)

        status, payload, ctype = self._proxy()
        if status == 404 and self.command == "GET" and \
                "text/html" in (self.headers.get("Accept") or ""):
            # The backend does not know this path and a browser asked for a
            # document: it is an SPA route.
            return self._send_file(os.path.join(ROOT, "index.html"))

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


print("serving %s and proxying %s on http://localhost:%d" % (ROOT, API, PORT),
      flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
