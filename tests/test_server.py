"""bserve against raw bytes written from the spec."""

import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import (DATA, END_STREAM, HEADERS, WWW, ServerTest, block, field, frame, get,  # noqa: E402
                     parse_block, temp_root)


def read_file(*parts):
    with open(os.path.join(*parts), "rb") as f:
        return f.read()


class StaticSiteTests(ServerTest):
    def test_get_index(self):
        c = self.client()
        c.send(get(b"/index.html"))
        status, headers, body, stream = c.read_response()
        self.assertEqual((status, stream), (200, 1))
        self.assertEqual(body, read_file(WWW, "index.html"))
        self.assertEqual(headers[b"content-type"], b"text/html; charset=utf-8")
        self.assertEqual(int(headers[b"content-length"]), len(body))
        self.assertIn(b"last-modified", headers)
        self.assertEqual(headers[b"server"], b"bserve/1.0")
        self.assertRegex(headers[b"date"], rb"^[A-Z][a-z]{2}, \d\d [A-Z][a-z]{2} \d{4} \d\d:\d\d:\d\d GMT$")

    def test_pseudo_header_comes_first_and_names_are_indexed(self):
        c = self.client()
        c.send(get(b"/style.css"))
        ftype, flags, stream, payload = c.read_frame()
        self.assertEqual(ftype, HEADERS)
        self.assertEqual(payload[:6], b"\x03\x00\x03200")   # index 3 = :status, then "200"
        self.assertEqual(payload[6], 7)                     # index 7 = content-type
        c.read_frame()

    def test_directory_serves_index(self):
        for path in (b"/", b"/docs", b"/docs/"):
            c = self.client()
            c.send(get(path))
            status, headers, body, _ = c.read_response()
            self.assertEqual(status, 200, path)
            self.assertIn(b"<title>", body)

    def test_query_string_is_ignored(self):
        c = self.client()
        c.send(get(b"/docs/notes.txt?x=1&y=2"))
        status, headers, body, _ = c.read_response()
        self.assertEqual(status, 200)
        self.assertEqual(headers[b"content-type"], b"text/plain; charset=utf-8")

    def test_percent_decoding(self):
        c = self.client()
        c.send(get(b"/docs/%6eotes.txt"))
        self.assertEqual(c.read_response()[0], 200)

    def test_404_has_a_text_body(self):
        c = self.client()
        c.send(get(b"/nope.html"))
        status, headers, body, _ = c.read_response()
        self.assertEqual(status, 404)
        self.assertEqual(body, b"404 Not Found\n")
        self.assertEqual(headers[b"content-type"], b"text/plain; charset=utf-8")
        self.assertEqual(headers[b"content-length"], b"14")

    def test_head_gets_headers_only(self):
        c = self.client()
        c.send(get(b"/index.html", method=b"HEAD"))
        ftype, flags, stream, payload = c.read_frame()
        self.assertEqual(ftype, HEADERS)
        self.assertTrue(flags & END_STREAM)
        self.assertIn(b"content-length", dict(parse_block(payload)))
        # nothing else follows: a second request proves the stream is clean
        c.send(get(b"/index.html", stream=3, method=b"HEAD"))
        self.assertEqual(c.read_response()[3], 3)

    def test_head_on_missing_file(self):
        c = self.client()
        c.send(get(b"/nope", method=b"HEAD"))
        status, headers, body, _ = c.read_response()
        self.assertEqual((status, body), (404, b""))

    def test_other_methods_get_405(self):
        c = self.client()
        c.send(get(b"/index.html", method=b"POST"))
        self.assertEqual(c.read_response()[0], 405)
        c.send(get(b"/index.html", stream=3, method=b"DELETE"))
        self.assertEqual(c.read_response()[0], 405)

    def test_request_body_is_drained_before_answering(self):
        c = self.client()
        c.send(get(b"/index.html", flags=0, method=b"POST"))
        c.send(frame(DATA, 0, 1, b"x" * 1000))
        c.send(frame(DATA, END_STREAM, 1, b"y"))
        self.assertEqual(c.read_response()[0], 405)
        c.send(get(b"/index.html", stream=3))
        self.assertEqual(c.read_response()[0], 200)


class KeepAliveTests(ServerTest):
    def test_many_requests_on_one_connection(self):
        c = self.client()
        for i, path in enumerate([b"/index.html", b"/nope", b"/style.css", b"/docs/notes.txt"]):
            sid = 2 * i + 1
            c.send(get(path, stream=sid))
            status, _, _, stream = c.read_response()
            self.assertEqual(stream, sid)
            self.assertEqual(status, 404 if path == b"/nope" else 200)

    def test_pipelined_requests_are_answered_in_order(self):
        c = self.client()
        c.send(get(b"/index.html", stream=1) + get(b"/nope", stream=3) + get(b"/style.css", stream=5))
        self.assertEqual([c.read_response()[3] for _ in range(3)], [1, 3, 5])

    def test_stream_ids_must_increase(self):
        c = self.client()
        c.send(get(b"/index.html", stream=5))
        self.assertEqual(c.read_response()[3], 5)
        c.send(get(b"/index.html", stream=3))
        self.assertTrue(c.closed())

    def test_even_and_zero_stream_ids_close_the_connection(self):
        for sid in (0, 2):
            c = self.client()
            c.send(get(b"/index.html", stream=sid))
            self.assertTrue(c.closed(), "stream %d" % sid)

    def test_bad_preface_closes_the_connection(self):
        c = self.client(preface=b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertTrue(c.closed())


class MalformedFrameTests(ServerTest):
    """Every one of these gets a 400 on its stream, and the next request still works."""

    def check_400_then_ok(self, bad_frame, bad_stream=1):
        c = self.client()
        c.send(bad_frame)
        status, headers, body, stream = c.read_response()
        self.assertEqual((status, stream), (400, bad_stream))
        self.assertEqual(body, b"400 Bad Request\n")
        c.send(get(b"/index.html", stream=bad_stream + 2))
        self.assertEqual(c.read_response()[0], 200)

    def test_undefined_static_index(self):
        payload = field(b":method", b"GET") + field(b":path", b"/") + field(b"zz", b"v", index=42)
        self.check_400_then_ok(frame(HEADERS, END_STREAM, 1, payload))

    def test_truncated_header_block(self):
        self.check_400_then_ok(frame(HEADERS, END_STREAM, 1, block((b":method", b"GET"))[:-1]))

    def test_missing_path(self):
        self.check_400_then_ok(frame(HEADERS, END_STREAM, 1, block((b":method", b"GET"))))

    def test_empty_headers_frame(self):
        self.check_400_then_ok(frame(HEADERS, END_STREAM, 1, b""))

    def test_relative_path(self):
        self.check_400_then_ok(get(b"index.html"))

    def test_pseudo_header_after_regular(self):
        payload = block((b":method", b"GET"), (b"host", b"h"), (b":path", b"/"))
        self.check_400_then_ok(frame(HEADERS, END_STREAM, 1, payload))

    def test_literal_pseudo_header(self):
        payload = block((b":method", b"GET"), (b":path", b"/")) + field(b":scheme", b"bh", index=0)
        self.check_400_then_ok(frame(HEADERS, END_STREAM, 1, payload))

    def test_stream_that_begins_with_data(self):
        self.check_400_then_ok(frame(DATA, END_STREAM, 1, b"hello"))

    def test_body_after_a_rejected_request_is_ignored(self):
        c = self.client()
        c.send(frame(HEADERS, 0, 1, block((b":method", b"GET"))))     # malformed, body to follow
        self.assertEqual(c.read_response()[0], 400)
        c.send(frame(DATA, 0, 1, b"late") + frame(DATA, END_STREAM, 1, b"later"))
        c.send(get(b"/index.html", stream=3))
        status, _, _, stream = c.read_response()
        self.assertEqual((status, stream), (200, 3))


class UnknownFrameTests(ServerTest):
    def test_unknown_types_are_skipped(self):
        c = self.client()
        for ftype in (0x02, 0x7F, 0xFF):
            c.send(frame(ftype, 0xFF, 0, os.urandom(300)))           # stream 0, all flags set
            c.send(frame(ftype, 0, 1, b"\x00" * 10))                  # on a stream, too
        c.send(get(b"/index.html"))
        self.assertEqual(c.read_response()[0], 200)

    def test_unknown_frame_inside_a_request_body(self):
        c = self.client()
        c.send(get(b"/index.html", flags=0, method=b"POST"))
        c.send(frame(0x50, 0, 1, b"ext"))
        c.send(frame(DATA, END_STREAM, 1, b""))
        self.assertEqual(c.read_response()[0], 405)

    def test_unknown_frame_with_maximum_length(self):
        c = self.client()
        c.send(frame(0x42, 0, 1, b"\xaa" * 65535))
        c.send(get(b"/index.html"))
        self.assertEqual(c.read_response()[0], 200)

    def test_unknown_flags_are_ignored(self):
        c = self.client()
        c.send(get(b"/index.html", flags=END_STREAM | 0xFE))
        self.assertEqual(c.read_response()[0], 200)


class FileMappingTests(ServerTest):
    @classmethod
    def setUpClass(cls):
        cls.root = temp_root()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.root)

    def test_large_file_spans_several_frames(self):
        c = self.client()
        c.send(get(b"/big.bin"))
        ftype, flags, _, _ = c.read_frame()
        self.assertEqual((ftype, flags & END_STREAM), (HEADERS, 0))
        sizes, body, flags = [], b"", 0
        while not flags & END_STREAM:
            ftype, flags, stream, payload = c.read_frame()
            self.assertEqual((ftype, stream), (DATA, 1))
            sizes.append(len(payload))
            body += payload
        self.assertEqual(body, read_file(self.root, "big.bin"))
        self.assertEqual(sizes, [65535, 65535, 65535, 200_000 - 3 * 65535])

    def test_empty_file_ends_on_the_headers_frame(self):
        c = self.client()
        c.send(get(b"/empty"))
        ftype, flags, _, payload = c.read_frame()
        self.assertEqual((ftype, flags), (HEADERS, END_STREAM))
        self.assertIn(b"\x08\x00\x010", payload)        # content-length: 0
        c.send(get(b"/index.html", stream=3))
        self.assertEqual(c.read_response()[0], 200)

    def test_dot_dot_cannot_leave_the_root(self):
        for path in (b"/../etc/passwd", b"/sub/../../etc/passwd", b"/%2e%2e/etc/passwd",
                     b"/..%2fetc/passwd", b"/sub/../index.html"):
            c = self.client()
            c.send(get(path))
            status, _, body, _ = c.read_response()
            if path == b"/sub/../index.html":
                self.assertEqual((status, body), (200, b"<p>hi</p>\n"), path)
            else:
                self.assertEqual(status, 404, path)

    def test_symlink_out_of_the_root_is_not_served(self):
        c = self.client()
        c.send(get(b"/escape"))
        self.assertEqual(c.read_response()[0], 404)

    def test_unknown_extension_is_octet_stream(self):
        c = self.client()
        c.send(get(b"/big.bin"))
        self.assertEqual(c.read_response()[1][b"content-type"], b"application/octet-stream")

    def test_nul_and_bad_utf8_in_path(self):
        for path in (b"/a%00b", b"/%ff%fe"):
            c = self.client()
            c.send(get(path))
            self.assertEqual(c.read_response()[0], 404, path)


if __name__ == "__main__":
    unittest.main()
