"""Run the Phase 0 benchmark defined in PROTOCOL.md. Writes results.json and results.csv."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pypdfium2 as pdfium

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from chonk import ocr, quality  # noqa: E402
from methods import METHODS  # noqa: E402

TARGETS = [100_000, 200_000, 300_000, 1_000_000, 2_000_000]
OCR_DPI = 300
MIN_CONFIDENT_CHARS = 200
TESSERACT = ocr.TesseractEngine("tesseract")


def page_words(pdf: bytes) -> list[list[ocr.Word]]:
    count = quality.page_count(pdf)
    return [TESSERACT.words(quality.render_page(pdf, i, OCR_DPI, "RGB")) for i in range(count)]


def confident_chars(words_by_page) -> int:
    return sum(len(ocr.normalise(t)) for page in words_by_page for t, c in page if c >= ocr.CONFIDENT)


def text_chars(pdf: bytes) -> int:
    document = pdfium.PdfDocument(pdf)
    try:
        total = 0
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            total += textpage.count_chars()
            textpage.close()
            page.close()
        return total
    finally:
        document.close()


def evaluate(job) -> dict:
    doc, target, method, source_path, original_words = job
    source = Path(source_path).read_bytes()
    started = time.perf_counter()
    try:
        output = METHODS[method](source, target)
        error = None
    except Exception as exc:  # a crash counts as no usable output
        output, error = None, type(exc).__name__
    seconds = time.perf_counter() - started
    row = {"doc": doc, "target": target, "method": method, "seconds": round(seconds, 2),
           "bytes": None if output is None else len(output), "fits": False, "error": error,
           "ocr_chars": None, "ocr_lost": None, "ocr_loss_pct": None, "worst_ssim": None,
           "text_chars_in": text_chars(source), "text_chars_out": None}
    if output is None:
        return row
    row["fits"] = len(output) <= target
    row["text_chars_out"] = text_chars(output)
    try:
        if quality.page_count(output) != quality.page_count(source):
            raise ValueError("page count changed")
        row["worst_ssim"] = round(quality.Reference(source, 150).compare(output).worst, 4)
        compressed_words = page_words(output)
        chars = lost = 0
        for original, compressed in zip(original_words, compressed_words):
            page_chars, page_lost = ocr.compare_words(original, compressed)
            chars += page_chars
            lost += page_lost
        row.update(ocr_chars=chars, ocr_lost=lost, ocr_loss_pct=round(100 * lost / chars, 2) if chars else None)
    except Exception as exc:
        row["error"] = f"measure:{type(exc).__name__}"
        row["ocr_loss_pct"] = 100.0
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--only", nargs="*", help="documents to include")
    args = parser.parse_args()
    corpus = sorted((HERE / "corpus").glob("*.pdf"))
    if args.only:
        corpus = [p for p in corpus if p.stem in args.only]

    originals, sanity = {}, {}
    for path in corpus:
        words = page_words(path.read_bytes())
        originals[path.stem] = words
        sanity[path.stem] = confident_chars(words)
        print(f"{path.stem}: {path.stat().st_size:,} bytes, {sanity[path.stem]} confident OCR chars", flush=True)
    too_hard = [d for d, n in sanity.items() if n < MIN_CONFIDENT_CHARS]
    if too_hard:
        print(f"PROTOCOL STOP: fewer than {MIN_CONFIDENT_CHARS} confident chars in {too_hard}", flush=True)
        return 2

    jobs = []
    for path in corpus:
        size = path.stat().st_size
        for target in TARGETS:
            if size <= target:
                continue  # excluded: already fits
            for method in METHODS:
                jobs.append((path.stem, target, method, str(path), originals[path.stem]))
    print(f"{len(jobs)} jobs", flush=True)
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(evaluate, jobs):
            rows.append(row)
            print(f"{row['doc']:<19} {row['target']:>9,} {row['method']:<13} fits={row['fits']!s:<5} "
                  f"bytes={row['bytes']} loss%={row['ocr_loss_pct']} ssim={row['worst_ssim']} {row['seconds']}s",
                  flush=True)
    (HERE / "results.json").write_text(json.dumps({"sanity": sanity, "rows": rows}, indent=1))
    with (HERE / "results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
