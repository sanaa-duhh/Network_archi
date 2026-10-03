"""Helpers shared by the tests.

Frames and header blocks are built here by hand, from the spec, with struct
and nothing from bh1.  That way the tests check the programs against the
document a stranger would read, not against the codec they share.
"""

import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BSERVE = os.path.join(ROOT, "bserve")
BCURL = os.path.join(ROOT, "bcurl")
WWW = os.path.join(ROOT, "www")

PREFACE = b"BH/1"
DATA, HEADERS = 0, 1
END_STREAM = 1

STATIC = {b":method": 1, b":path": 2, b":status": 3, b"host": 4, b"user-agent": 5,
          b"accept": 6, b"content-type": 7, b"content-length": 8, b"date": 9, b"server": 10}


def frame(ftype, flags, stream, payload=b""):
    return struct.pack("!HBBI", len(payload), ftype, flags, stream) + payload


def field(name, value, index=None):
    """One header field.  index=None looks the name up; an int forces that index."""
    if index is None:
        index = STATIC.get(name, 0)
    head = bytes([index]) if index else bytes([0, len(name)]) + name
    return head + struct.pack("!H", len(value)) + value


def block(*fields):
    return b"".join(field(n, v) for n, v in fields)


def get(path, stream=1, flags=END_STREAM, method=b"GET", extra=()):
    fields = [(b":method", method), (b":path", path), (b"host", b"test")] + list(extra)
    return frame(HEADERS, flags, stream, block(*fields))


def parse_block(payload):
    """Independent decoder: list of (name, value)."""
    fields, pos = [], 0
    while pos < len(payload):
        index = payload[pos]
        pos += 1
        if index == 0:
            n = payload[pos]
            name = payload[pos + 1:pos + 1 + n]
            pos += 1 + n
        else:
            name = {v: k for k, v in STATIC.items()}[index]
        (vlen,) = struct.unpack("!H", payload[pos:pos + 2])
        pos += 2
        fields.append((name, payload[pos:pos + vlen]))
        pos += vlen
    return fields


class Client:
    """A raw socket speaking BH/1 byte by byte."""

    def __init__(self, port, preface=PREFACE, timeout=5.0):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        if preface is not None:
            self.sock.sendall(preface)

    def send(self, data):
        self.sock.sendall(data)

    def read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                return buf
            buf += chunk
        return buf

    def read_frame(self):
        """(type, flags, stream, payload), or None if the server closed."""
        head = self.read_exact(8)
        if len(head) < 8:
            return None
        length, ftype, flags, stream = struct.unpack("!HBBI", head)
        payload = self.read_exact(length)
        assert len(payload) == length, "short payload"
        return ftype, flags, stream, payload

    def read_response(self):
        """(status, headers, body, stream) for one complete response."""
        ftype, flags, stream, payload = self.read_frame()
        assert ftype == HEADERS, "expected HEADERS, got type %d" % ftype
        fields = parse_block(payload)
        assert fields[0][0] == b":status"
        status = int(fields[0][1])
        headers = dict(fields[1:])
        body = b""
        while not flags & END_STREAM:
            ftype, flags, s, payload = self.read_frame()
            assert ftype == DATA and s == stream, "unexpected frame type %d on stream %d" % (ftype, s)
            body += payload
        return status, headers, body, stream

    def closed(self):
        """True if the server has closed the connection (no bytes, then EOF)."""
        try:
            return self.sock.recv(1) == b""
        except ConnectionResetError:
            return True      # closed with our bytes still unread: the kernel sends RST
        except socket.timeout:
            return False

    def close(self):
        self.sock.close()


class ServerTest(unittest.TestCase):
    """Starts one bserve for the test class, on a free port."""

    root = WWW

    @classmethod
    def setUpClass(cls):
        cls.proc = subprocess.Popen([sys.executable, BSERVE, cls.root, "0"],
                                    stderr=subprocess.PIPE, text=True)
        line = cls.proc.stderr.readline()
        m = re.search(r"listening on port (\d+)", line)
        if not m:
            cls.proc.kill()
            raise RuntimeError("bserve did not start: %r" % line)
        cls.port = int(m.group(1))

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()

    def client(self, **kw):
        c = Client(self.port, **kw)
        self.addCleanup(c.close)
        return c


def temp_root():
    """A fresh root with a few files, including one bigger than a frame."""
    d = tempfile.mkdtemp(prefix="bh1-")
    with open(os.path.join(d, "index.html"), "wb") as f:
        f.write(b"<p>hi</p>\n")
    with open(os.path.join(d, "big.bin"), "wb") as f:
        f.write(os.urandom(200_000))
    with open(os.path.join(d, "empty"), "wb"):
        pass
    os.mkdir(os.path.join(d, "sub"))
    with open(os.path.join(d, "sub", "a.txt"), "wb") as f:
        f.write(b"a\n")
    os.symlink("/etc/hosts", os.path.join(d, "escape"))
    return d
