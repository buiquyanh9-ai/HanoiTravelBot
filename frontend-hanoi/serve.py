"""Independent UI: python frontend-hanoi/serve.py (backend stays on port 8000)."""
import argparse
import json
import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def proxy(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 32000:
            self.send_error(413)
            return
        body = self.rfile.read(length) if self.command == 'POST' else None
        request = Request(self.server.backend + self.path, data=body,
                          headers={'Content-Type': 'application/json'}, method=self.command)
        try:
            with urlopen(request, timeout=150) as response:
                status, data = response.status, response.read()
        except HTTPError as error:
            status, data = error.code, error.read()
        except (URLError, TimeoutError, OSError):
            status = 502
            data = json.dumps({'error': 'Chưa kết nối được backend. Hãy chạy python -m hanoibot.server --port 8000.'}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith('/api/'):
            self.proxy()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/'):
            self.proxy()
        else:
            self.send_error(404)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8081)
    parser.add_argument('--backend', default='http://127.0.0.1:8000')
    parser.add_argument('--start-backend', action='store_true', help='Start a separate backend on port 8090')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    backend_process = None
    if args.start_backend:
        args.backend = 'http://127.0.0.1:8090'
        backend_process = subprocess.Popen(
            [sys.executable, '-m', 'hanoibot.server', '--port', '8090'], cwd=ROOT.parent)
        for attempt in range(40):
            if backend_process.poll() is not None:
                raise SystemExit('Backend failed to start; see backend output above.')
            try:
                with urlopen(args.backend + '/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except (URLError, OSError):
                time.sleep(0.25)
        else:
            backend_process.terminate()
            raise SystemExit('Backend did not become ready.')
    server.backend = args.backend.rstrip('/')
    print(f'Hanoi UI: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if backend_process is not None:
            backend_process.terminate()
            backend_process.wait(timeout=10)
