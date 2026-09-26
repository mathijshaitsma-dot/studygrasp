"""Statische server voor de StudyGrasp-frontend.

Zelfde als `python -m http.server`, maar met no-cache headers zodat de
browser na een code-update nooit oude JS/CSS-bestanden blijft gebruiken
(dat gaf een zwart scherm door een mix van oude en nieuwe modules).

Gebruik:  python frontend/serve.py [poort]   (default 5173)
"""
import functools
import http.server
import os
import sys


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):  # rustige console
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5173
    directory = os.path.dirname(os.path.abspath(__file__))
    handler = functools.partial(NoCacheHandler, directory=directory)
    print(f"StudyGrasp frontend draait op http://localhost:{port}")
    http.server.ThreadingHTTPServer(("", port), handler).serve_forever()
