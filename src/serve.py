#!/usr/bin/env python3
"""Serve src/ as static files with SPA fallback (so /join?t=... hits index.html)."""

import argparse
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))


class SPAHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        target = self.translate_path(path)
        if not os.path.exists(target):
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *args):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="待会儿办前端静态服务器")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5173)
    args = parser.parse_args()

    handler = partial(SPAHandler, directory=ROOT)
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"前端已启动： http://localhost:{args.port}/  (Ctrl+C 停止)")
    print("加入入口示例： http://localhost:%d/join?t=<家庭令牌>" % args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
