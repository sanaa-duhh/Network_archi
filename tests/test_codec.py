"""Unit tests for bh1.py against hand-built bytes."""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bh1  # noqa: E402

from support import block, field  # noqa: E402


class FrameTests(unittest.TestCase):
    def test_header_layout_is_length_type_flags_stream(self):
        f = bh1.Frame(bh1.HEADERS, bh1.END_STREAM, 7, b"xyz")
        self.assertEqual(f.to_bytes(), b"\x00\x03\x01\x01\x00\x00\x00\x07xyz")

    def test_payload_cannot_exceed_16_bits(self):
        bh1.Frame(bh1.DATA, 0, 1, b"x" * 65535).to_bytes()
        with self.assertRaises(ValueError):
            bh1.Frame(bh1.DATA, 0, 1, b"x" * 65536).to_bytes()

    def test_describe_names_unknown_flags(self):
        self.assertEqual(bh1.describe_flags(0), "0")
        self.assertEqual(bh1.describe_flags(0x01), "END_STREAM")
        self.assertEqual(bh1.describe_flags(0x81), "END_STREAM|0x80")


class HeaderBlockTests(unittest.TestCase):
    def test_indexed_and_literal_round_trip(self):
        fields = [(b":method", b"GET"), (b":path", b"/"), (b"x-custom", b"v")]
        encoded = bh1.encode_headers(fields)
        self.assertEqual(encoded, block(*fields))
        self.assertEqual(bh1.decode_headers(encoded), fields)

    def test_static_indexes_match_the_spec(self):
        self.assertEqual(bh1.encode_headers([(b"server", b"s")]), b"\x0a\x00\x01s")
        self.assertEqual(bh1.encode_headers([(b"last-modified", b"x")]),
                         b"\x00\x0dlast-modified\x00\x01x")

    def test_undefined_index_is_malformed(self):
        for index in (11, 0x80, 0xff):
            with self.assertRaises(bh1.MalformedError):
                bh1.decode_headers(field(b"whatever", b"v", index=index))

    def test_truncation_anywhere_is_malformed(self):
        first = block((b":method", b"GET"))
        good = first + block((b"x", b"yy"))
        for cut in range(1, len(good)):
            if cut == len(first):
                continue        # a cut on a field boundary is just a shorter block
            with self.assertRaises(bh1.MalformedError, msg="cut at %d" % cut):
                bh1.decode_headers(good[:cut])

    def test_literal_name_rules(self):
        for bad in (b":status", b":x", b"Upper", b"has space", b"a\x00b"):
            with self.assertRaises(bh1.MalformedError, msg=bad):
                bh1.decode_headers(field(bad, b"v", index=0))
        with self.assertRaises(bh1.MalformedError):
            bh1.decode_headers(b"\x00\x00" + struct.pack("!H", 0))   # empty literal name

    def test_empty_block_decodes_to_nothing(self):
        self.assertEqual(bh1.decode_headers(b""), [])


class RequestValidationTests(unittest.TestCase):
    def test_minimal_request(self):
        m, p, rest = bh1.validate_request([(b":method", b"GET"), (b":path", b"/a")])
        self.assertEqual((m, p, rest), (b"GET", b"/a", []))

    def test_missing_pseudo_headers(self):
        with self.assertRaises(bh1.MalformedError):
            bh1.validate_request([(b":method", b"GET")])
        with self.assertRaises(bh1.MalformedError):
            bh1.validate_request([(b":path", b"/")])

    def test_pseudo_after_regular_or_duplicated(self):
        with self.assertRaises(bh1.MalformedError):
            bh1.validate_request([(b":method", b"GET"), (b"host", b"h"), (b":path", b"/")])
        with self.assertRaises(bh1.MalformedError):
            bh1.validate_request([(b":method", b"GET"), (b":path", b"/"), (b":path", b"/")])

    def test_status_in_request(self):
        with self.assertRaises(bh1.MalformedError):
            bh1.validate_request([(b":method", b"GET"), (b":path", b"/"), (b":status", b"200")])

    def test_path_and_method_syntax(self):
        for path in (b"", b"index.html", b"/a b", b"/\xff"):
            with self.assertRaises(bh1.MalformedError, msg=path):
                bh1.validate_request([(b":method", b"GET"), (b":path", path)])
        for method in (b"", b"GE T", b"G\x00"):
            with self.assertRaises(bh1.MalformedError, msg=method):
                bh1.validate_request([(b":method", method), (b":path", b"/")])

    def test_content_length(self):
        self.assertEqual(bh1.content_length([(b"content-length", b"12")]), 12)
        self.assertIsNone(bh1.content_length([]))
        with self.assertRaises(bh1.MalformedError):
            bh1.content_length([(b"content-length", b"1"), (b"content-length", b"2")])
        with self.assertRaises(bh1.MalformedError):
            bh1.content_length([(b"content-length", b"-1")])


class ResponseValidationTests(unittest.TestCase):
    def test_status_must_be_three_digits(self):
        self.assertEqual(bh1.validate_response([(b":status", b"404")])[0], 404)
        for bad in (b"", b"20", b"2000", b"OK!"):
            with self.assertRaises(bh1.MalformedError, msg=bad):
                bh1.validate_response([(b":status", bad)])

    def test_request_pseudo_in_response(self):
        with self.assertRaises(bh1.MalformedError):
            bh1.validate_response([(b":status", b"200"), (b":path", b"/")])


class HexdumpTests(unittest.TestCase):
    def test_layout(self):
        out = bh1.hexdump(b"BH/1" + bytes(range(13)), "> ")
        lines = out.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("> 0000  42 48 2f 31 00 01 02 03  04 05 06 07 08 09 0a 0b  |BH/1"))
        self.assertTrue(lines[1].startswith("> 0010  0c"))
        self.assertTrue(lines[1].endswith("|.|"))


if __name__ == "__main__":
    unittest.main()
