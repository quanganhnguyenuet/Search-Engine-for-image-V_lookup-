"""Downloads of the CSV import, with both clients, against a local server."""

import socketserver
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from visualseek_retrieval import page_context

PICTURE = b"p" * 1000


def _reply(path: str) -> bytes:
    def head(status: str, kind: str, extra: str = "") -> bytes:
        lines = f"HTTP/1.1 {status}\r\nContent-Type: {kind}\r\n{extra}"
        return f"{lines}Connection: close\r\n\r\n".encode()

    if path == "/picture":
        return head("200 OK", "image/jpeg") + PICTURE
    if path == "/huge":
        return head("200 OK", "image/jpeg") + b"h" * 2_000_000
    if path == "/page":
        return head("200 OK", "text/html") + b"t" * 2_000_000
    if path == "/moved":
        return head("302 Found", "text/plain", "Location: /picture\r\n")
    if path == "/missing":
        return head("404 Not Found", "text/plain")
    return head("200 OK", "image/jpeg", "Content-Length: 100000\r\n") + b"cut short"


ENDLESS_BYTES = 128 * 1024 * 1024


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            path = self.request.recv(65536).split(b" ")[1].decode()
            if path == "/endless":
                self._endless()
            else:
                self.request.sendall(_reply(path))
        except (OSError, IndexError):  # the client stops reading at its size limit
            pass

    def _endless(self):
        """A very large body; ``sent`` is how much the client took before it hung up."""
        sent, block = 0, b"e" * 65536
        try:
            self.request.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nConnection: close\r\n\r\n"
            )
            while sent < ENDLESS_BYTES:
                self.request.sendall(block)
                sent += len(block)
        finally:
            self.server.sent = sent
            self.server.hung_up.set()


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    request_queue_size = 64


@pytest.fixture(scope="module")
def server():
    server = _Server(("127.0.0.1", 0), _Handler)
    server.sent, server.hung_up = 0, threading.Event()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def base_url(server):
    return f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture(params=["curl_cffi", "urllib"])
def client(request, monkeypatch):
    """Which session() every thread of the test gets."""
    if request.param == "curl_cffi":
        cr = pytest.importorskip("curl_cffi.requests")

        def make():
            return cr.Session(impersonate="chrome", trust_env=False)

    else:
        make = page_context._UrllibSession
    local = threading.local()

    def session():
        if not hasattr(local, "s"):
            local.s = make()
        return local.s

    monkeypatch.setattr(page_context, "session", session)
    monkeypatch.setattr(page_context, "check_public", lambda url: "127.0.0.1")
    return request.param


def test_image_download_follows_redirects_and_returns_the_whole_body(base_url, client):
    body, final_url, kind = page_context.fetch_image(f"{base_url}/moved", 5000)
    assert (body, final_url, kind) == (PICTURE, f"{base_url}/picture", "image/jpeg")
    assert page_context.fetch_image(f"{base_url}/picture", len(PICTURE))[0] == PICTURE


def test_image_over_the_limit_is_refused(base_url, client):
    with pytest.raises(ValueError, match="vượt giới hạn"):
        page_context.fetch_image(f"{base_url}/huge", 100_000)


def test_the_body_stops_at_the_limit(base_url, client):
    response, body, truncated = page_context.download(f"{base_url}/huge", {}, 100_000)
    assert (response.status_code, len(body), truncated) == (200, 100_000, True)


def test_the_transfer_is_aborted_at_the_limit(server, base_url, client):
    """Not only cut afterwards: the rest of the body is never downloaded."""
    server.hung_up.clear()
    assert page_context.download(f"{base_url}/endless", {}, 100_000)[2]
    assert server.hung_up.wait(30)
    assert server.sent < ENDLESS_BYTES // 2


def test_long_page_is_cut_not_failed(base_url, client, monkeypatch):
    monkeypatch.setattr(page_context, "MAX_BYTES", 100_000)
    status, _, kind, body, headers = page_context.get(f"{base_url}/page", page_context.Polite(0))
    assert (status, kind, len(body)) == (200, "text/html", 100_000)
    assert headers


def test_http_error_is_reported(base_url, client):
    with pytest.raises(ValueError, match="HTTP 404"):
        page_context.fetch_image(f"{base_url}/missing", 5000)


def test_parallel_downloads_with_failures(base_url, client):
    """Six threads as in the import. With stream=True this killed the interpreter."""

    def one(index: int) -> str:
        path = ["/picture", "/huge", "/moved", "/missing", "/short"][index % 5]
        try:
            return "ok" if page_context.fetch_image(base_url + path, 100_000)[0] else "empty"
        except Exception:
            return "refused"

    with ThreadPoolExecutor(6) as pool:
        outcomes = list(pool.map(one, range(300)))
    assert outcomes[0] == outcomes[2] == "ok"
    assert outcomes[1] == outcomes[3] == "refused"


def test_only_image_types_pillow_can_read_are_asked_for():
    """Pillow 10 has no AVIF reader: asking for AVIF got pictures the import then dropped."""
    from PIL import Image

    Image.init()
    asked = {part.split(";")[0] for part in page_context.IMAGE_HEADERS["Accept"].split(",")}
    assert asked - set(Image.MIME.values()) - {"image/apng", "image/*", "*/*"} == set()
