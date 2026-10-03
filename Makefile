PYTHON ?= python3
PORT   ?= 9000
CHROME ?= /Applications/Google Chrome.app/Contents/MacOS/Google Chrome

.PHONY: test serve spec clean

test:
	$(PYTHON) -m unittest discover -s tests

serve:
	./bserve ./www $(PORT)

# SPEC.md -> SPEC.pdf: pandoc for the HTML, a headless browser for the paging.
spec: SPEC.pdf

SPEC.pdf: SPEC.md spec.css
	pandoc SPEC.md -s --css spec.css --embed-resources -o SPEC.html
	"$(CHROME)" --headless --disable-gpu --no-pdf-header-footer \
	    --print-to-pdf=SPEC.pdf file://$(CURDIR)/SPEC.html 2>/dev/null
	rm -f SPEC.html

clean:
	rm -rf SPEC.html SPEC.pdf tests/__pycache__ __pycache__
