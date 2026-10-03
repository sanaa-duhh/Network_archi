"""Codec for BH/1, the binary HTTP protocol described in SPEC.md.

bserve and bcurl both import this file.  It turns frames and header blocks
into bytes and back, and reads one frame off a socket.  Bytes that do not
follow the spec raise MalformedError; the caller decides whether that is a
stream error (answer 400) or a connection error (close).
"""

import struct
from typing import NamedTuple

PREFACE = b"BH/1"

FRAME_HEADER = struct.Struct("!HBBI")   # length, type, flags, stream id
FRAME_HEADER_LEN = FRAME_HEADER.size    # 8
MAX_PAYLOAD = 0xFFFF                    # every value the length field can hold is legal

# Frame types.  A receiver skips any type it does not know (SPEC.md, section 3).
DATA = 0x00
HEADERS = 0x01
TYPE_NAMES = {DATA: "DATA", HEADERS: "HEADERS"}

# Flags.  A receiver ignores any bit it does not know.
END_STREAM = 0x01
FLAG_NAMES = {END_STREAM: "END_STREAM"}

# Static header table.  Index = position + 1; index 0 means a literal name follows.
STATIC_TABLE = (
    b":method", b":path", b":status", b"host", b"user-agent",
    b"accept", b"content-type", b"content-length", b"date", b"server",
)
STATIC_INDEX = {name: i + 1 for i, name in enumerate(STATIC_TABLE)}
PSEUDO_HEADERS = frozenset((b":method", b":path", b":status"))

# RFC 9110 tchar, lower-case only, as HTTP/2 requires of field names.
_NAME_CHARS = frozenset(b"!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyz")
_METHOD_CHARS = _NAME_CHARS | frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ")


class MalformedError(ValueError):
    """A frame payload or header block that does not follow the spec."""


class ConnectionClosed(EOFError):
    """The peer closed the connection in the middle of a frame."""


class ProtocolError(Exception):
    """A frame that is well formed but arrives where the spec forbids it."""


class Frame(NamedTuple):
    type: int
    flags: int
    stream: int
    payload: bytes = b""

    def to_bytes(self):
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError("payload longer than %d bytes" % MAX_PAYLOAD)
        return FRAME_HEADER.pack(len(self.payload), self.type, self.flags, self.stream) + self.payload

    def describe(self):
        name = TYPE_NAMES.get(self.type, "type 0x%02x (unknown, skipped)" % self.type)
        return "%s stream=%d flags=%s length=%d" % (
            name, self.stream, describe_flags(self.flags), len(self.payload))


def describe_flags(flags):
    names = [name for bit, name in FLAG_NAMES.items() if flags & bit]
    rest = flags & ~sum(FLAG_NAMES)
    if rest:
        names.append("0x%02x" % rest)
    return "|".join(names) or "0"


# --- socket I/O ---------------------------------------------------------------

def read_exact(sock, n):
    """Read exactly n bytes.  Returns b"" on a clean close before the first byte."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 65536))
        if not chunk:
            if buf:
                raise ConnectionClosed("connection closed after %d of %d bytes" % (len(buf), n))
            return b""
        buf += chunk
    return bytes(buf)


def read_frame(sock):
    """Read one frame.  Returns None if the peer closed at a frame boundary."""
    head = read_exact(sock, FRAME_HEADER_LEN)
    if not head:
        return None
    length, ftype, flags, stream = FRAME_HEADER.unpack(head)
    payload = b""
    if length:
        payload = read_exact(sock, length)
        if not payload:
            raise ConnectionClosed("connection closed after a frame header promising %d bytes" % length)
    return Frame(ftype, flags, stream, payload)


# --- header blocks --------------------------------------------------------------

def encode_headers(fields):
    """fields: sequence of (name, value) byte strings, in the order to send."""
    out = bytearray()
    for name, value in fields:
        index = STATIC_INDEX.get(name, 0)
        if index:
            out.append(index)
        else:
            if not 1 <= len(name) <= 255:
                raise ValueError("literal name must be 1..255 bytes")
            out.append(0)
            out.append(len(name))
            out += name
        if len(value) > 0xFFFF:
            raise ValueError("value longer than 65535 bytes")
        out += struct.pack("!H", len(value))
        out += value
    return bytes(out)


def decode_headers(block):
    """Returns a list of (name, value) in wire order; raises MalformedError."""
    fields = []
    pos = 0
    end = len(block)

    def take(k, what):
        nonlocal pos
        if pos + k > end:
            raise MalformedError("header block ends inside a %s" % what)
        piece = block[pos:pos + k]
        pos += k
        return piece

    while pos < end:
        index = take(1, "name index")[0]
        if index == 0:
            nlen = take(1, "name length")[0]
            if nlen == 0:
                raise MalformedError("literal name of length 0")
            name = bytes(take(nlen, "literal name"))
            _check_literal_name(name)
        elif index <= len(STATIC_TABLE):
            name = STATIC_TABLE[index - 1]
        else:
            raise MalformedError("static index %d is not defined" % index)
        (vlen,) = struct.unpack("!H", take(2, "value length"))
        value = bytes(take(vlen, "value"))
        fields.append((name, value))
    return fields


def _check_literal_name(name):
    if name[0] == 0x3A:  # ':'
        raise MalformedError("literal name %r starts with ':'" % name)
    if any(c not in _NAME_CHARS for c in name):
        raise MalformedError("header name %r is not a lower-case token" % name)


def _split_pseudo(fields):
    pseudo = {}
    regular = []
    for name, value in fields:
        if name in PSEUDO_HEADERS:
            if regular:
                raise MalformedError("%r after a regular field" % name)
            if name in pseudo:
                raise MalformedError("duplicate %r" % name)
            pseudo[name] = value
        else:
            regular.append((name, value))
    return pseudo, regular


def content_length(fields):
    """The content-length value as an int, or None if absent.  Raises if invalid."""
    values = {v for n, v in fields if n == b"content-length"}
    if not values:
        return None
    if len(values) > 1:
        raise MalformedError("conflicting content-length values")
    value = values.pop()
    if not value.isdigit():
        raise MalformedError("content-length %r is not a decimal number" % value)
    return int(value)


def validate_request(fields):
    """Returns (method, path, regular_fields) or raises MalformedError."""
    pseudo, regular = _split_pseudo(fields)
    if b":status" in pseudo:
        raise MalformedError(":status in a request")
    for required in (b":method", b":path"):
        if required not in pseudo:
            raise MalformedError("request without %s" % required.decode())
    method, path = pseudo[b":method"], pseudo[b":path"]
    if not method or any(c not in _METHOD_CHARS for c in method):
        raise MalformedError(":method %r is not a token" % method)
    if not path.startswith(b"/") or any(not 0x21 <= c <= 0x7E for c in path):
        raise MalformedError(":path %r is not an absolute path" % path)
    content_length(regular)
    return method, path, regular


def validate_response(fields):
    """Returns (status, regular_fields) or raises MalformedError."""
    pseudo, regular = _split_pseudo(fields)
    if b":method" in pseudo or b":path" in pseudo:
        raise MalformedError("request pseudo-header in a response")
    status = pseudo.get(b":status")
    if status is None:
        raise MalformedError("response without :status")
    if len(status) != 3 or not status.isdigit():
        raise MalformedError(":status %r is not three digits" % status)
    content_length(regular)
    return int(status), regular


# --- debugging ------------------------------------------------------------------

def hexdump(data, prefix=""):
    """hexdump -C style: offset, 8+8 hex bytes, ASCII column."""
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        left = " ".join("%02x" % b for b in chunk[:8])
        right = " ".join("%02x" % b for b in chunk[8:])
        text = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append("%s%04x  %-23s  %-23s  |%s|" % (prefix, off, left, right, text))
    return "\n".join(lines)
