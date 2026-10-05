"""Synthetic image/provider fixtures; no user images, real secrets or external requests."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from types import SimpleNamespace
import threading

SECRET = "fixture-vision-key-not-real"
REF = "secret://workspace/vision_fixture"


def image(directory):
    from PIL import Image
    path = Path(directory) / "synthetic.jpg"
    photo = Image.new("RGB", (640, 320), "#23aabb")
    exif = Image.Exif(); exif[270] = "PRIVATE_METADATA_NOT_TO_SEND"; exif[274] = 6
    photo.save(path, exif=exif)
    return path


def response():
    value = {"result": {"summary": "Synthetic blue panel", "criteria_met": None},
        "evidence": [{"kind": "observation", "description": "Blue pixels", "region": {"x": 0, "y": 0, "width": 1, "height": 1}}],
        "issues": ["Synthetic fixture; no real model inference."]}
    return {"id": "resp_fixture", "status": "completed", "model": "gpt-4.1-mini-2025-04-14",
        "output": [{"type": "message", "role": "assistant", "status": "completed", "content": [
            {"type": "output_text", "text": json.dumps(value), "annotations": []}]}]}


@contextmanager
def provider():
    fixture = SimpleNamespace(requests=[], mode="ok", release=threading.Event(), disconnected=threading.Event())
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            assert 0 < size < 16000000
            raw = self.rfile.read(size)
            body = json.loads(raw)
            fixture.requests.append({"body": body, "authorization": self.headers.get("Authorization"), "path": self.path})
            mode = fixture.mode
            if mode == "hold":
                fixture.release.wait(8)
            data = response()
            if mode == "incomplete":
                data["status"] = "incomplete"
            if mode == "bad_schema":
                data["output"][0]["content"][0]["text"] = '{}'
            if mode == "refusal":
                data["output"][0]["content"] = [{"type": "refusal", "refusal": "Raw private refusal text"}]
            payload = json.dumps(data).encode()
            code = 429 if mode == "rate" else 302 if mode == "redirect" else 200
            if mode == "large":
                payload = b'x' * 300000
            self.send_response(code)
            if mode == "redirect":
                self.send_header("Location", "/never-follow")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                fixture.disconnected.set()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    fixture.url = "http://127.0.0.1:" + str(server.server_address[1]) + "/v1/responses"
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield fixture
    finally:
        fixture.release.set(); server.shutdown(); server.server_close(); thread.join(2)


def prepare_http_fixture(directory, fixture):
    """Instrument only disposable processes; production package endpoint stays immutable."""
    source = '''import httpx
_vision_original_stream = httpx.AsyncClient.stream
def _vision_fixture_stream(self, method, url, *args, **kwargs):
    if str(url) == "https://api.openai.com/v1/responses":
        url = FIXTURE_URL
    elif not str(url).startswith(("http://127.0.0.1:", "http://localhost:")):
        raise RuntimeError("External networking is forbidden in the Vision test fixture")
    return _vision_original_stream(self, method, url, *args, **kwargs)
httpx.AsyncClient.stream = _vision_fixture_stream
'''.replace("FIXTURE_URL", repr(fixture.url))
    (Path(directory) / "sitecustomize.py").write_text(source, encoding="utf-8")
