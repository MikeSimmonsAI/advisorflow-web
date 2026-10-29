"""Serve a built frontend (dist/) on 127.0.0.1 with the SPA fallback.
Usage: python -I serve_spa.py /path/to/dist 4173"""
import http.server
import os
import sys

ROOT = os.path.abspath(sys.argv[1])
PORT = int(sys.argv[2])


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def send_head(self):
        path = self.translate_path(self.path.split("?", 1)[0])
        if not os.path.exists(path) or (os.path.isdir(path) and not os.path.exists(os.path.join(path, "index.html"))):
            self.path = "/index.html"
        return super().send_head()

    def log_message(self, *a):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
