"""Apply PROTOCOL.md's decision rule to results.json, and render comparison crops."""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

USABLE_LOSS = 2.0
COMPETITORS = ["gs_ebook", "gs_screen", "gs_ladder", "raster_target"]


def usable(row) -> bool:
    return bool(row["fits"]) and row["ocr_loss_pct"] is not None and row["ocr_loss_pct"] <= USABLE_LOSS


def summarise(rows):
    by_method = defaultdict(list)
    for row in rows:
        by_method[row["method"]].append(row)
    cells = len({(r["doc"], r["target"]) for r in rows})
    table = {}
    for method, items in by_method.items():
        fits = [r for r in items if r["fits"]]
        losses = [r["ocr_loss_pct"] for r in fits if r["ocr_loss_pct"] is not None]
        ssims = [r["worst_ssim"] for r in fits if r["worst_ssim"] is not None]
        born_digital = [r for r in items if r["doc"] == "digital_with_photo" and r["fits"]]
        table[method] = {
            "cells": len(items),
            "fit_rate": 100 * len(fits) / cells,
            "usable_rate": 100 * sum(usable(r) for r in items) / cells,
            "median_loss_when_fits": statistics.median(losses) if losses else None,
            "median_ssim_when_fits": statistics.median(ssims) if ssims else None,
            "text_layer_kept": f"{sum((r['text_chars_out'] or 0) > 0 for r in born_digital)}/{len(born_digital)}",
        }
    return cells, table


def decide(rows, table):
    # Tie-break (not specified in the protocol; stated in the report): higher
    # usable rate, then higher fit rate, then lower median loss.
    best = max(COMPETITORS, key=lambda m: (table[m]["usable_rate"], table[m]["fit_rate"],
                                            -(table[m]["median_loss_when_fits"] or 1e9)))
    index = {(r["doc"], r["target"], r["method"]): r for r in rows}
    shared, chonk_better, improvements = [], 0, []
    for (doc, target, method), row in index.items():
        if method != "chonk":
            continue
        other = index.get((doc, target, best))
        if not (row["fits"] and other and other["fits"]):
            continue
        if row["ocr_loss_pct"] is None or other["ocr_loss_pct"] is None:
            continue
        shared.append((doc, target))
        improvement = other["ocr_loss_pct"] - row["ocr_loss_pct"]
        improvements.append(improvement)
        chonk_better += improvement > 0
    usable_gap = table["chonk"]["usable_rate"] - table[best]["usable_rate"]
    median_improvement = statistics.median(improvements) if improvements else 0.0
    go = usable_gap >= 15 or (shared and chonk_better >= len(shared) / 2 and median_improvement >= 2)
    no_go = usable_gap <= 5 and abs(median_improvement) < 1
    verdict = "GO" if go else "NO-GO" if no_go else "WEAK"
    return {"best_competitor": best, "usable_gap_points": usable_gap, "shared_fit_cells": len(shared),
            "chonk_lower_loss_in": chonk_better, "median_loss_improvement_points": median_improvement,
            "verdict": verdict}


def markdown(rows, cells, table, decision) -> str:
    lines = [f"Included cells: {cells}", "",
             "| Method | Fit rate | Usable-fit rate | Median OCR loss when it fits | Median worst-page SSIM when it fits | Text layer kept (born-digital) |",
             "|---|---|---|---|---|---|"]
    for method in COMPETITORS + ["chonk"]:
        t = table[method]
        loss = "n/a" if t["median_loss_when_fits"] is None else f"{t['median_loss_when_fits']:.2f}%"
        ssim = "n/a" if t["median_ssim_when_fits"] is None else f"{t['median_ssim_when_fits']:.3f}"
        lines.append(f"| `{method}` | {t['fit_rate']:.0f}% | **{t['usable_rate']:.0f}%** | {loss} | {ssim} | {t['text_layer_kept']} |")
    lines += ["", "Decision inputs: " + json.dumps(decision), "",
              "| Document | Target | " + " | ".join(f"`{m}`" for m in COMPETITORS + ["chonk"]) + " |",
              "|---|---|" + "---|" * (len(COMPETITORS) + 1)]
    index = {(r["doc"], r["target"], r["method"]): r for r in rows}
    for doc, target in sorted({(r["doc"], r["target"]) for r in rows}):
        cells_md = []
        for method in COMPETITORS + ["chonk"]:
            r = index[(doc, target, method)]
            if r["bytes"] is None:
                cells_md.append("no output")
                continue
            mark = "✅" if usable(r) else ("⚠️" if r["fits"] else "❌")
            loss = "?" if r["ocr_loss_pct"] is None else f"{r['ocr_loss_pct']:.1f}%"
            cells_md.append(f"{mark} {r['bytes'] / 1000:.0f} KB, loss {loss}")
        lines.append(f"| {doc} | {target // 1000:,} KB | " + " | ".join(cells_md) + " |")
    lines += ["", "✅ usable fit (fits and OCR loss ≤ 2%) · ⚠️ fits but loses more text · ❌ over the limit"]
    return "\n".join(lines)


CROPS = {  # regions at 300 dpi, page 0
    "passport_scan": (190, 1040, 1660, 1240),     # machine-readable zone
    "marksheet_scan": (300, 720, 2180, 1060),     # 8 pt table text
    "phone_photo": (700, 1400, 2300, 1900),       # photographed body text
}


def crops(rows, out_dir: Path) -> None:
    from PIL import Image, ImageDraw
    from methods import METHODS
    from chonk import quality

    out_dir.mkdir(exist_ok=True)
    for doc, box in CROPS.items():
        source = (HERE / "corpus" / f"{doc}.pdf").read_bytes()
        doc_rows = [r for r in rows if r["doc"] == doc]
        hardest = min((r["target"] for r in doc_rows), default=None)
        if hardest is None:
            continue
        tiles = [("original", source)]
        for method in COMPETITORS + ["chonk"]:
            row = next(r for r in doc_rows if r["method"] == method and r["target"] == hardest)
            output = METHODS[method](source, hardest) if row["bytes"] is not None else None
            label = f"{method}: {row['bytes'] / 1000:.0f} KB, OCR loss {row['ocr_loss_pct']}%" if output else f"{method}: no output"
            tiles.append((label, output))
        width, height = box[2] - box[0], box[3] - box[1]
        sheet = Image.new("RGB", (width, (height + 50) * len(tiles)), "white")
        draw = ImageDraw.Draw(sheet)
        for i, (label, pdf) in enumerate(tiles):
            y = i * (height + 50)
            draw.text((10, y + 10), label + (f"  (target {hardest // 1000} KB)" if i else ""), fill="black")
            if pdf is not None:
                page = quality.render_page(pdf, 0, 300, "RGB")
                sheet.paste(page.crop(box), (0, y + 45))
        sheet.save(out_dir / f"{doc}_{hardest // 1000}KB.png")


def main() -> None:
    data = json.loads((HERE / "results.json").read_text())
    rows = data["rows"]
    cells, table = summarise(rows)
    decision = decide(rows, table)
    text = markdown(rows, cells, table, decision)
    (HERE / "results.md").write_text(text + "\n")
    print(text)
    if "--crops" in sys.argv:
        crops(rows, HERE / "crops")


if __name__ == "__main__":
    main()
