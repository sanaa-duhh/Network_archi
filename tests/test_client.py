"""bcurl against bserve, and against a scripted server that misbehaves on purpose."""

import os
import socket
import struct
import subprocess
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import (BCURL, DATA, END_STREAM, HEADERS, PREFACE, WWW, ServerTest, block,  # noqa: E402
                     frame, parse_block)


def bcurl(*args, timeout=10):
    return subprocess.run([sys.executable, BCURL] + list(args), capture_output=True, timeout=timeout)


class AgainstBserve(ServerTest):
    def url(self, path):
        return "localhost:%d%s" % (self.port, path)

    def test_body_to_stdout_exit_zero(self):
        r = bcurl(self.url("/index.html"))
        with open(os.path.join(WWW, "index.html"), "rb") as f:
            self.assertEqual(r.stdout, f.read())
        self.assertEqual((r.returncode, r.stderr), (0, b""))

    def test_verbose_hexdumps_every_frame(self):
        r = bcurl("-v", self.url("/index.html"))
        err = r.stderr.decode()
        self.assertEqual(r.returncode, 0)
        self.assertIn("> preface (4 bytes)\n> 0000  42 48 2f 31", err)
        self.assertIn("> HEADERS stream=1 flags=END_STREAM length=", err)
        self.assertIn("< HEADERS stream=1 flags=0 length=", err)
        self.assertIn("< DATA stream=1 flags=END_STREAM length=212", err)
        self.assertIn("> :path: /index.html", err)
        self.assertIn("< :status: 200", err)
        self.assertIn("< last-modified: ", err)
        # the frame header of the request, byte for byte
        self.assertIn("> 0000  00 ", err)

    def test_404_exits_22_and_prints_the_body(self):
        r = bcurl(self.url("/missing"))
        self.assertEqual(r.returncode, 22)
        self.assertEqual(r.stdout, b"404 Not Found\n")

    def test_head(self):
        r = bcurl("-I", self.url("/index.html"))
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith(b":status: 200\ncontent-type: text/html"))

    def test_several_urls_share_one_connection(self):
        r = bcurl("-v", self.url("/docs/notes.txt"), self.url("/missing"), self.url("/docs/notes.txt"))
        err = r.stderr.decode()
        self.assertEqual(r.returncode, 22)
        self.assertEqual(err.count("* connected to"), 1)
        self.assertIn("> HEADERS stream=1 ", err)
        self.assertIn("> HEADERS stream=3 ", err)
        self.assertIn("> HEADERS stream=5 ", err)
        self.assertEqual(r.stdout, b"plain text, served as text/plain\n404 Not Found\n"
                                   b"plain text, served as text/plain\n")

    def test_urls_on_different_servers_are_refused(self):
        r = bcurl(self.url("/a"), "localhost:%d/b" % (self.port + 1))
        self.assertEqual(r.returncode, 2)

    def test_unparseable_url(self):
        self.assertEqual(bcurl("http://x/y").returncode, 2)
        self.assertEqual(bcurl("").returncode, 2)

    def test_connection_refused(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        r = bcurl("localhost:%d/" % port)
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"cannot connect", r.stderr)


class ScriptedServer:
    """Accepts one connection, reads the preface and one frame, writes a script of bytes."""

    def __init__(self, reply, linger=False):
        self.reply = reply
        self.linger = linger
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.received = b""
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        conn, _ = self.listener.accept()
        with conn:
            conn.settimeout(5)
            self.received = conn.recv(65536)
            conn.sendall(self.reply)
            try:
                while self.linger and conn.recv(65536):   # stay open until the client hangs up
                    pass
            except OSError:
                pass
        self.listener.close()


def response(status, body, stream=1, extra=()):
    fields = [(b":status", status), (b"content-length", str(len(body)).encode())] + list(extra)
    return frame(HEADERS, 0, stream, block(*fields)) + frame(DATA, END_STREAM, stream, body)


class AgainstScriptedServer(unittest.TestCase):
    def fetch(self, reply, *args, paths=("/x",)):
        srv = ScriptedServer(reply, linger=len(paths) > 1)
        r = bcurl(*args, *["127.0.0.1:%d%s" % (srv.port, p) for p in paths])
        srv.thread.join(5)
        self.port = srv.port
        return r, srv.received

    def test_request_bytes_follow_the_spec(self):
        r, received = self.fetch(response(b"200", b"ok"))
        self.assertEqual(received[:4], PREFACE)
        payload = received[12:]
        self.assertEqual(received[4:12], struct.pack("!HBBI", len(payload), HEADERS, END_STREAM, 1))
        fields = parse_block(payload)
        self.assertEqual(fields, [(b":method", b"GET"), (b":path", b"/x"),
                                  (b"host", b"127.0.0.1:%d" % self.port),
                                  (b"user-agent", b"bcurl/1.0"), (b"accept", b"*/*")])
        self.assertEqual((r.returncode, r.stdout), (0, b"ok"))

    def test_unknown_frames_from_the_server_are_skipped(self):
        junk = frame(0x7E, 0xFF, 0, b"settings?") + frame(0x09, 0, 1, b"\x00" * 100)
        reply = junk + frame(HEADERS, 0, 1, block((b":status", b"200"))) + junk + \
            frame(DATA, 0, 1, b"ab") + junk + frame(DATA, END_STREAM, 1, b"c")
        r, _ = self.fetch(reply, "-v")
        self.assertEqual((r.returncode, r.stdout), (0, b"abc"))
        self.assertIn("type 0x7e (unknown, skipped)", r.stderr.decode())

    def test_body_split_over_many_frames(self):
        body = os.urandom(70_000)
        reply = frame(HEADERS, 0, 1, block((b":status", b"200"))) + frame(DATA, 0, 1, body[:65535]) + \
            frame(DATA, 0, 1, b"") + frame(DATA, END_STREAM, 1, body[65535:])
        r, _ = self.fetch(reply)
        self.assertEqual((r.returncode, r.stdout), (0, body))

    def test_server_error_codes_exit_22(self):
        for status in (b"400", b"404", b"500"):
            r, _ = self.fetch(response(status, b"no"))
            self.assertEqual((r.returncode, r.stdout), (22, b"no"), status)

    def test_headers_only_response(self):
        r, _ = self.fetch(frame(HEADERS, END_STREAM, 1, block((b":status", b"204"))))
        self.assertEqual((r.returncode, r.stdout), (0, b""))

    def test_content_length_mismatch_is_an_error(self):
        r, _ = self.fetch(response(b"200", b"short", extra=[]).replace(b"\x00\x015", b"\x00\x019"))
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"content-length said 9 bytes, body had 5", r.stderr)

    def test_connection_closed_mid_response(self):
        r, _ = self.fetch(frame(HEADERS, 0, 1, block((b":status", b"200"))) + b"\x00\x10\x00\x01")
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"connection closed", r.stderr)

    def test_malformed_response_headers(self):
        r, _ = self.fetch(frame(HEADERS, END_STREAM, 1, b"\x63\x00\x00"))
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"static index 99 is not defined", r.stderr)

    def test_response_on_the_wrong_stream(self):
        r, _ = self.fetch(response(b"200", b"ok", stream=3))
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"HEADERS on stream 3 while waiting on stream 1", r.stderr)

    def test_stale_data_on_a_closed_stream_is_ignored(self):
        reply = response(b"200", b"one", stream=1) + frame(DATA, END_STREAM, 1, b"stale") + \
            response(b"200", b"two", stream=3)
        r, _ = self.fetch(reply, paths=("/x", "/y"))
        self.assertEqual((r.returncode, r.stdout), (0, b"onetwo"))

    def test_data_on_stream_zero_is_an_error(self):
        r, _ = self.fetch(frame(DATA, 0, 0, b"?") + response(b"200", b"ok"))
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"DATA on stream 0", r.stderr)

    def test_text_http_server(self):
        r, _ = self.fetch(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
