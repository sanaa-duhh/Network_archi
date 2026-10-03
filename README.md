# BH/1

A binary HTTP: HTTP's methods, paths, status codes and header fields, carried as length-prefixed frames over one TCP connection. Course project for Network Architecture.

| File | What it is |
|---|---|
| `SPEC.md` / `SPEC.pdf` | The protocol, two pages. Everything a second implementation needs. |
| `HEXDUMP.md` | One complete request and response, annotated byte by byte. |
| `bserve` | The server: `./bserve ./www 9000` |
| `bcurl` | The client: `./bcurl -v localhost:9000/index.html` |
| `bh1.py` | The codec both programs import: frame and header-block encoding, validation, hexdump. |
| `www/` | A small site to serve. |
| `tests/` | 72 tests. The protocol tests speak to `bserve` and `bcurl` in raw bytes built from the spec, not through `bh1.py`. |

Python 3.8 or later, standard library only. Nothing to install.

## Running

```
$ ./bserve ./www 9000
2026-10-02 17:16:45 listening on port 9000, serving /…/www
```

```
$ ./bcurl localhost:9000/index.html          # body to stdout
$ ./bcurl -v localhost:9000/index.html       # plus a hexdump of every frame on stderr
$ ./bcurl -I localhost:9000/index.html       # HEAD: response headers only
$ ./bcurl localhost:9000/a.html localhost:9000/b.css   # two requests, one connection
```

`bcurl` exits 0 on success, 22 if the server answered 4xx or 5xx (the code `curl -f` uses), 1 if the connection or the protocol failed, 2 on a usage error.

```
$ make test
```

## Trying the protocol by hand

A request is the preface, then one HEADERS frame. This is `GET /` with no other headers, 4 + 8 + 10 bytes:

```
$ printf 'BH/1\x00\x0a\x01\x01\x00\x00\x00\x01\x01\x00\x03GET\x02\x00\x01/' | nc localhost 9000 | hexdump -C
```
