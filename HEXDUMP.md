# One request and response, byte by byte

Captured from `./bcurl -v localhost:9000/index.html` against `./bserve ./www 9000`. Three things crossed the wire: the preface and one HEADERS frame from the client, then a HEADERS frame and a DATA frame from the server. Offsets are relative to each frame. Section numbers refer to SPEC.md.

## Client → server: preface (§1)

```
0000  42 48 2f 31                                       |BH/1|
```

| Bytes | Meaning |
|---|---|
| `42 48 2f 31` | ASCII `BH/1`: this connection speaks BH/1. Sent once, before the first frame. |

## Client → server: HEADERS, stream 1 (63 bytes)

```
0000  00 37 01 01 00 00 00 01  01 00 03 47 45 54 02 00  |.7.........GET..|
0010  0b 2f 69 6e 64 65 78 2e  68 74 6d 6c 04 00 0e 6c  |./index.html...l|
0020  6f 63 61 6c 68 6f 73 74  3a 39 30 30 30 05 00 09  |ocalhost:9000...|
0030  62 63 75 72 6c 2f 31 2e  30 06 00 03 2a 2f 2a     |bcurl/1.0...*/*|
```

Frame header (§2):

| Offset | Bytes | Field | Value |
|---|---|---|---|
| 0000 | `00 37` | length | 55 payload bytes follow |
| 0002 | `01` | type | HEADERS |
| 0003 | `01` | flags | END_STREAM: the request has no body |
| 0004 | `00 00 00 01` | stream id | 1, the client's first stream |

Header block (§3), five fields, every name indexed:

| Offset | Bytes | Field | Decoded |
|---|---|---|---|
| 0008 | `01` | index 1 | `:method` |
| 0009 | `00 03` | value length | 3 |
| 000b | `47 45 54` | value | `GET` |
| 000e | `02` | index 2 | `:path` |
| 000f | `00 0b` | value length | 11 |
| 0011 | `2f 69 6e 64 65 78 2e 68 74 6d 6c` | value | `/index.html` |
| 001c | `04` | index 4 | `host` |
| 001d | `00 0e` | value length | 14 |
| 001f | `6c 6f 63 61 6c 68 6f 73 74 3a 39 30 30 30` | value | `localhost:9000` |
| 002d | `05` | index 5 | `user-agent` |
| 002e | `00 09` | value length | 9 |
| 0030 | `62 63 75 72 6c 2f 31 2e 30` | value | `bcurl/1.0` |
| 0039 | `06` | index 6 | `accept` |
| 003a | `00 03` | value length | 3 |
| 003c | `2a 2f 2a` | value | `*/*` |

The block ends at offset 003f, exactly where the frame length said it would. The five names would have cost 37 bytes literal (32 of name, 5 of length) and cost 5 here.

## Server → client: HEADERS, stream 1 (138 bytes)

```
0000  00 82 01 00 00 00 00 01  03 00 03 32 30 30 07 00  |...........200..|
0010  18 74 65 78 74 2f 68 74  6d 6c 3b 20 63 68 61 72  |.text/html; char|
0020  73 65 74 3d 75 74 66 2d  38 08 00 03 32 31 32 09  |set=utf-8...212.|
0030  00 1d 46 72 69 2c 20 30  32 20 4f 63 74 20 32 30  |..Fri, 02 Oct 20|
0040  32 36 20 31 31 3a 34 36  3a 34 36 20 47 4d 54 0a  |26 11:46:46 GMT.|
0050  00 0a 62 73 65 72 76 65  2f 31 2e 30 00 0d 6c 61  |..bserve/1.0..la|
0060  73 74 2d 6d 6f 64 69 66  69 65 64 00 1d 46 72 69  |st-modified..Fri|
0070  2c 20 30 32 20 4f 63 74  20 32 30 32 36 20 31 31  |, 02 Oct 2026 11|
0080  3a 34 33 3a 34 31 20 47  4d 54                    |:43:41 GMT|
```

Frame header:

| Offset | Bytes | Field | Value |
|---|---|---|---|
| 0000 | `00 82` | length | 130 |
| 0002 | `01` | type | HEADERS |
| 0003 | `00` | flags | no END_STREAM: a body follows in DATA frames |
| 0004 | `00 00 00 01` | stream id | 1: this answers the request above |

Header block, six fields. The first five are indexed; the last is a literal, since `last-modified` is not one of the ten:

| Offset | Bytes | Field | Decoded |
|---|---|---|---|
| 0008 | `03` | index 3 | `:status` (first, as a pseudo-header must be) |
| 0009 | `00 03` | value length | 3 |
| 000b | `32 30 30` | value | `200` |
| 000e | `07` | index 7 | `content-type` |
| 000f | `00 18` | value length | 24 |
| 0011 | `74 65 78 74 2f 68 74 6d 6c 3b 20 63 68 61 72 73 65 74 3d 75 74 66 2d 38` | value | `text/html; charset=utf-8` |
| 0029 | `08` | index 8 | `content-length` |
| 002a | `00 03` | value length | 3 |
| 002c | `32 31 32` | value | `212` (decimal digits, not a binary integer: one codec for every value) |
| 002f | `09` | index 9 | `date` |
| 0030 | `00 1d` | value length | 29 |
| 0032 | `46 72 69 2c 20 30 32 20 4f 63 74 20 32 30 32 36 20 31 31 3a 34 36 3a 34 36 20 47 4d 54` | value | `Fri, 02 Oct 2026 11:46:46 GMT` |
| 004f | `0a` | index 10 | `server` |
| 0050 | `00 0a` | value length | 10 |
| 0052 | `62 73 65 72 76 65 2f 31 2e 30` | value | `bserve/1.0` |
| 005c | `00` | index 0 | a literal name follows |
| 005d | `0d` | name length | 13 |
| 005e | `6c 61 73 74 2d 6d 6f 64 69 66 69 65 64` | name | `last-modified` |
| 006b | `00 1d` | value length | 29 |
| 006d | `46 72 69 2c 20 30 32 20 4f 63 74 20 32 30 32 36 20 31 31 3a 34 33 3a 34 31 20 47 4d 54` | value | `Fri, 02 Oct 2026 11:43:41 GMT` |

The block ends at 008a, 130 bytes after it began. A receiver that did not know index 0 could not have found the name's length; a receiver that did not know `last-modified` still reads it fine, because the name is spelled out.

## Server → client: DATA, stream 1 (220 bytes)

```
0000  00 d4 00 01 00 00 00 01  3c 21 44 4f 43 54 59 50  |........<!DOCTYP|
0010  45 20 68 74 6d 6c 3e 0a  3c 68 74 6d 6c 20 6c 61  |E html>.<html la|
0020  6e 67 3d 22 65 6e 22 3e  0a 3c 68 65 61 64 3e 0a  |ng="en">.<head>.|
0030  3c 6d 65 74 61 20 63 68  61 72 73 65 74 3d 22 75  |<meta charset="u|
0040  74 66 2d 38 22 3e 0a 3c  74 69 74 6c 65 3e 62 73  |tf-8">.<title>bs|
0050  65 72 76 65 3c 2f 74 69  74 6c 65 3e 0a 3c 6c 69  |erve</title>.<li|
0060  6e 6b 20 72 65 6c 3d 22  73 74 79 6c 65 73 68 65  |nk rel="styleshe|
0070  65 74 22 20 68 72 65 66  3d 22 2f 73 74 79 6c 65  |et" href="/style|
0080  2e 63 73 73 22 3e 0a 3c  2f 68 65 61 64 3e 0a 3c  |.css">.</head>.<|
0090  62 6f 64 79 3e 0a 3c 68  31 3e 49 74 20 77 6f 72  |body>.<h1>It wor|
00a0  6b 73 2e 3c 2f 68 31 3e  0a 3c 70 3e 53 65 72 76  |ks.</h1>.<p>Serv|
00b0  65 64 20 6f 76 65 72 20  42 48 2f 31 20 62 79 20  |ed over BH/1 by |
00c0  62 73 65 72 76 65 2e 3c  2f 70 3e 0a 3c 2f 62 6f  |bserve.</p>.</bo|
00d0  64 79 3e 0a 3c 2f 68 74  6d 6c 3e 0a              |dy>.</html>.|
```

| Offset | Bytes | Field | Value |
|---|---|---|---|
| 0000 | `00 d4` | length | 212, which is what `content-length` promised |
| 0002 | `00` | type | DATA |
| 0003 | `01` | flags | END_STREAM: last frame of the response; stream 1 is now closed |
| 0004 | `00 00 00 01` | stream id | 1 |
| 0008 | `3c 21 44 4f …` | payload | the 212 bytes of `www/index.html`, untouched |

A file larger than 65535 bytes would arrive as several DATA frames on the same stream, END_STREAM set only on the last.

## Totals

| Direction | Bytes | Of which framing |
|---|---|---|
| client → server | 4 + 63 = 67 | 4 preface + 8 header |
| server → client | 138 + 220 = 358 | 2 × 8 header |

The same exchange in HTTP/1.1 is 86 bytes of request and 183 of response head. BH/1 is not smaller because of the binary header (its 8 bytes replace `GET … HTTP/1.1\r\n` and the blank line) but because names are one byte and values carry no `: ` or `\r\n`; what it gives up is the ability to read the wire without a tool like `bcurl -v`.
