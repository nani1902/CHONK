Included cells: 22

| Method | Fit rate | Usable-fit rate | Median OCR loss when it fits | Median worst-page SSIM when it fits | Text layer kept (born-digital) |
|---|---|---|---|---|---|
| `gs_ebook` | 45% | **36%** | 1.28% | 0.950 | 0/0 |
| `gs_screen` | 77% | **5%** | 27.96% | 0.791 | 1/1 |
| `gs_ladder` | 100% | **50%** | 3.30% | 0.940 | 3/3 |
| `raster_target` | 100% | **59%** | 0.79% | 0.926 | 0/3 |
| `chonk` | 100% | **68%** | 0.37% | 0.968 | 3/3 |

Decision inputs: {"best_competitor": "raster_target", "usable_gap_points": 9.090909090909093, "shared_fit_cells": 22, "chonk_lower_loss_in": 9, "median_loss_improvement_points": 0.0, "verdict": "WEAK"}

| Document | Target | `gs_ebook` | `gs_screen` | `gs_ladder` | `raster_target` | `chonk` |
|---|---|---|---|---|---|---|
| certificate_scan | 100 KB | ❌ 174 KB, loss 0.0% | ⚠️ 67 KB, loss 18.3% | ⚠️ 67 KB, loss 18.3% | ✅ 99 KB, loss 0.0% | ✅ 100 KB, loss 0.0% |
| certificate_scan | 200 KB | ✅ 174 KB, loss 0.0% | ⚠️ 67 KB, loss 18.3% | ✅ 174 KB, loss 0.0% | ✅ 199 KB, loss 0.0% | ✅ 186 KB, loss 0.0% |
| certificate_scan | 300 KB | ✅ 174 KB, loss 0.0% | ⚠️ 67 KB, loss 18.3% | ✅ 174 KB, loss 0.0% | ✅ 299 KB, loss 0.0% | ✅ 277 KB, loss 0.0% |
| certificate_scan | 1,000 KB | ✅ 174 KB, loss 0.0% | ⚠️ 67 KB, loss 18.3% | ✅ 174 KB, loss 0.0% | ✅ 616 KB, loss 0.0% | ✅ 875 KB, loss 0.0% |
| certificate_scan | 2,000 KB | ✅ 174 KB, loss 0.0% | ⚠️ 67 KB, loss 18.3% | ✅ 174 KB, loss 0.0% | ✅ 616 KB, loss 0.0% | ✅ 1982 KB, loss 0.0% |
| digital_with_photo | 100 KB | ❌ 877 KB, loss 0.0% | ❌ 200 KB, loss 0.0% | ✅ 51 KB, loss 0.0% | ⚠️ 100 KB, loss 22.4% | ✅ 94 KB, loss 0.0% |
| digital_with_photo | 200 KB | ❌ 877 KB, loss 0.0% | ❌ 200 KB, loss 0.0% | ✅ 121 KB, loss 0.0% | ✅ 194 KB, loss 0.1% | ✅ 187 KB, loss 0.0% |
| digital_with_photo | 300 KB | ❌ 877 KB, loss 0.0% | ✅ 200 KB, loss 0.0% | ✅ 200 KB, loss 0.0% | ✅ 298 KB, loss 0.1% | ✅ 280 KB, loss 0.0% |
| marksheet_scan | 100 KB | ❌ 388 KB, loss 5.1% | ❌ 170 KB, loss 69.8% | ⚠️ 67 KB, loss 88.4% | ⚠️ 98 KB, loss 82.7% | ⚠️ 94 KB, loss 42.9% |
| marksheet_scan | 200 KB | ❌ 388 KB, loss 5.1% | ⚠️ 170 KB, loss 69.8% | ⚠️ 170 KB, loss 69.8% | ⚠️ 197 KB, loss 12.1% | ⚠️ 189 KB, loss 8.9% |
| marksheet_scan | 300 KB | ❌ 388 KB, loss 5.1% | ⚠️ 170 KB, loss 69.8% | ⚠️ 170 KB, loss 69.8% | ⚠️ 300 KB, loss 5.9% | ⚠️ 295 KB, loss 6.8% |
| marksheet_scan | 1,000 KB | ⚠️ 388 KB, loss 5.1% | ⚠️ 170 KB, loss 69.8% | ⚠️ 388 KB, loss 5.1% | ⚠️ 972 KB, loss 6.7% | ⚠️ 820 KB, loss 6.7% |
| marksheet_scan | 2,000 KB | ⚠️ 388 KB, loss 5.1% | ⚠️ 170 KB, loss 69.8% | ⚠️ 388 KB, loss 5.1% | ⚠️ 1793 KB, loss 7.1% | ⚠️ 1881 KB, loss 7.4% |
| passport_scan | 100 KB | ❌ 326 KB, loss 1.3% | ❌ 129 KB, loss 54.6% | ⚠️ 54 KB, loss 90.6% | ⚠️ 99 KB, loss 18.5% | ⚠️ 98 KB, loss 22.0% |
| passport_scan | 200 KB | ❌ 326 KB, loss 1.3% | ⚠️ 129 KB, loss 54.6% | ⚠️ 129 KB, loss 54.6% | ✅ 199 KB, loss 1.6% | ✅ 182 KB, loss 1.4% |
| passport_scan | 300 KB | ❌ 326 KB, loss 1.3% | ⚠️ 129 KB, loss 54.6% | ⚠️ 129 KB, loss 54.6% | ✅ 299 KB, loss 0.9% | ✅ 282 KB, loss 0.9% |
| passport_scan | 1,000 KB | ✅ 326 KB, loss 1.3% | ⚠️ 129 KB, loss 54.6% | ✅ 326 KB, loss 1.3% | ✅ 910 KB, loss 0.7% | ✅ 822 KB, loss 0.7% |
| passport_scan | 2,000 KB | ✅ 326 KB, loss 1.3% | ⚠️ 129 KB, loss 54.6% | ✅ 326 KB, loss 1.3% | ✅ 1285 KB, loss 0.7% | ✅ 1852 KB, loss 0.7% |
| phone_photo | 100 KB | ❌ 288 KB, loss 1.5% | ❌ 107 KB, loss 28.0% | ⚠️ 53 KB, loss 90.8% | ⚠️ 99 KB, loss 2.6% | ⚠️ 98 KB, loss 9.3% |
| phone_photo | 200 KB | ❌ 288 KB, loss 1.5% | ⚠️ 107 KB, loss 28.0% | ⚠️ 107 KB, loss 28.0% | ✅ 197 KB, loss 0.5% | ✅ 186 KB, loss 0.1% |
| phone_photo | 300 KB | ✅ 288 KB, loss 1.5% | ⚠️ 107 KB, loss 28.0% | ✅ 288 KB, loss 1.5% | ⚠️ 298 KB, loss 2.1% | ✅ 272 KB, loss 0.1% |
| phone_photo | 1,000 KB | ✅ 288 KB, loss 1.5% | ⚠️ 107 KB, loss 28.0% | ✅ 288 KB, loss 1.5% | ✅ 863 KB, loss 0.1% | ✅ 965 KB, loss 0.1% |

✅ usable fit (fits and OCR loss ≤ 2%) · ⚠️ fits but loses more text · ❌ over the limit
