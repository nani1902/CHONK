# Phase 0 result: WEAK

**Question** (from [`PROTOCOL.md`](PROTOCOL.md), committed before any result
existed, `6737130`): on the small targets real portals use, does CHONK produce
*usable* files (under the limit **and** OCR loss ≤ 2%) meaningfully more often
than the strategies common compressors use?

**Answer under the pre-registered rule: WEAK.** CHONK has the best usable-fit
rate, 68% against 59% for the best competitor, a simple "rasterise and
binary-search JPEG quality" tool. The gap is 9 points, below the 15-point GO
bar. Where both fit, CHONK lost less text in only 9 of 22 cells, and the median
improvement was 0.

The rule's instruction for WEAK: **proceed only with the agent-native niche and
portal presets, not a general "better compressor" pitch.**

## Results

22 cells (5 synthetic documents × targets from 100 KB to 2 MB, excluding
targets the source already met), 5 methods, 110 runs. Full per-cell table:
[`results.md`](results.md). Raw data: [`results.json`](results.json).

| Method | Fit rate | **Usable-fit rate** | Median OCR loss when it fits | Median worst-page SSIM | Text layer kept (born-digital) | Median time |
|---|---|---|---|---|---|---|
| `gs_ebook` (one preset) | 45% | 36% | 1.28% | 0.950 | none fit | about 0.2 s |
| `gs_screen` (one preset) | 77% | 5% | 27.96% | 0.791 | 1/1 | about 0.2 s |
| `gs_ladder` (harder presets until it fits) | 100% | 50% | 3.30% | 0.940 | 3/3 | under 1 s |
| `raster_target` (rasterise, binary-search quality) | 100% | 59% | 0.79% | 0.926 | **0/3** | 0.33 s |
| `chonk` | 100% | **68%** | 0.37% | 0.968 | 3/3 | **6.9 s** |

Decision inputs: best competitor `raster_target`; usable-fit gap +9.1 points;
22 shared-fit cells; CHONK lower OCR loss in 9 of them; median improvement 0.0.

## What holds up

1. **Preset tools really are bad at small targets.** A tool that steps down
   presets until the file fits jumps straight to `/screen`-class settings and
   destroys text (18–90% OCR loss) when a gentler setting would have fitted. In
   the crops, the ladder's passport machine-readable zone and marksheet digits
   are smeared. This is the failure CHONK was built to fix.
2. **A 40-line competitor nearly matches CHONK on scans.** `raster_target`
   (render, JPEG, binary-search the quality) gets within 9 points overall and
   beats CHONK in some cells: phone photo at 100 KB (2.6% vs 9.3%), passport at
   100 KB (18.5% vs 22.0%). CHONK's compression advantage on scans is real but
   small: 2 cells of 22.
3. **CHONK clearly wins on born-digital PDFs.** It keeps the text layer (3/3;
   `raster_target` 0/3) and at 100 KB loses 0% of text against 22.4%. Searchable
   text disappearing silently is a real cost of rasterising tools.
4. **At the hardest targets nobody produces a clean file.** Multi-page 300 dpi
   scans at 100 KB lose 18–90% of confidently read text with every method. What
   is valuable there is *telling the user*, which only CHONK's verification
   does, plus suggesting a remedy (grayscale, fewer pages, a larger limit).
5. **CHONK optimises the wrong target for legibility.** It picks candidates by
   SSIM, and in the cells where it lost to `raster_target`, the SSIM winner was
   not the OCR winner. A text-aware objective (OCR in the loop, or a
   stroke-sharpness measure) is the obvious engine improvement. This comes from
   inspecting the results afterwards; it was not part of the protocol.
6. **CHONK is about 20× slower.** Median 6.9 s against 0.33 s on a server CPU.
   On a mid-range phone (about 4× slower, per the browser test) that is roughly
   30 seconds against about 1 second. That is a real handicap for a web app.

## Checks and limitations

- **OCR noise floor (found after the run).** On `marksheet_scan`, a
  near-lossless re-encode at full resolution (97% of the original bytes) already
  "loses" 3.5% on OCR, so the 2% threshold sat below that document's noise
  floor, and no method could score a usable fit on it. The protocol did not
  anticipate a per-document noise floor. **Sensitivity check:** excluding the
  marksheet, CHONK has 88% usable fits and `raster_target` 76%, a 12-point gap.
  Still WEAK; the verdict does not depend on this document.
- **OCR is conservative at hard targets.** In the crops, passport and marksheet
  outputs at 100 KB with 18–43% "OCR loss" are still readable by a person for
  both CHONK and `raster_target`. The metric overstates damage there, but it
  does not change the ranking.
- **Competitors are proxies, not the real websites.** Hosted tools couldn't be
  tested without uploading documents. Real sites may be better or worse than
  these strategies.
- **The corpus is small and synthetic.** 5 documents and 22 cells. A 9-point
  gap is 2 cells. Confidence in the exact numbers is low; confidence in the
  direction ("small edge on scans, clear edge on born-digital, big edge over
  preset ladders") is moderate.
- **One OCR engine** (Tesseract 5.3.4).
- **Conflict of interest.** CHONK's author also wrote the benchmark.
  Pre-registration, the frozen engine and publishing every cell are the
  mitigations; they are not a substitute for an independent replication.

## Visual check

Crops at each document's hardest target, one row per method:
[`crops/passport_scan_100KB.png`](crops/passport_scan_100KB.png),
[`crops/marksheet_scan_100KB.png`](crops/marksheet_scan_100KB.png),
[`crops/phone_photo_100KB.png`](crops/phone_photo_100KB.png).

## Reproduce

```bash
python bench/corpus.py                # regenerates the synthetic corpus (fixed seeds)
python bench/run.py --workers 4       # needs Ghostscript and Tesseract
python bench/analyze.py --crops
```
