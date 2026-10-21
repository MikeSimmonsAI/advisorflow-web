import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
OUT = sys.argv[1]
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with open(OUT, "a") as f:
            f.write(json.dumps({"path": self.path, "ctype": self.headers.get("Content-Type"), "body": json.loads(body)}) + "\n")
        out = json.dumps({"success": True, "reference": "WB-STUB-1"}).encode()
        self.send_response(201); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", 8765), H).serve_forever()
