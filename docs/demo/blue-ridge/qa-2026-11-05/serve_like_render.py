"""Static server approximating Render static-site behavior: existing files win, directories
serve their index.html, everything else is rewritten to /index.html (SPA fallback)."""
import http.server, os, sys
ROOT = os.path.abspath(sys.argv[1]); PORT = int(sys.argv[2])

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=ROOT, **k)
    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path) and not os.path.exists(os.path.join(path, 'index.html')):
            self.path = '/index.html'
        elif not os.path.exists(path):
            self.path = '/index.html'
        return super().send_head()
    def log_message(self, *a): pass

http.server.ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
