# 0001 — Licensing and distribution for the pilot

- Status: **Proposed — owner decision required.** Nothing in this record changes CHONK's license. It does not authorize a commercial pilot until the *Sign-off* section is completed.
- Task: CHONK-002. Blocks CHONK-024 (bundles) and CHONK-026 (pilot).
- Date: 2026-09-25. Reviewed tree: `6509a56`.
- Not legal advice. The analysis below reads license texts; it does not substitute for counsel in the jurisdictions where the pilot runs. Confidence levels are stated per finding.

## 1. Question

Can CHONK be used in the planned pilot with the intended users, and how may CHONK plus its dependencies be distributed, **under the license terms that apply today**? Which decisions must the owner make before a commercial pilot or a binary bundle?

## 2. Current terms (verified)

Sources: the repository's [`LICENSE`](../../LICENSE); the PolyForm Noncommercial 1.0.0 text from the [PolyForm project repository at tag `1.0.0`](https://github.com/polyformproject/polyform-licenses/blob/1.0.0/PolyForm-Noncommercial-1.0.0.md); the Ghostscript [`LICENSE`](https://github.com/ArtifexSoftware/ghostpdl/blob/master/LICENSE); and the license metadata/files inside the wheels resolved on 2026-09-25 from the unpinned `requirements.txt` (`pypdf 6.19.0`, `pypdfium2 5.13.0` linux_x64, `Pillow 12.3.0` cp311 manylinux) plus `PyInstaller 6.22.3` (used by the README's bundle command).

### 2.1 CHONK itself

| Fact | Evidence | Confidence |
|---|---|---|
| CHONK is offered under PolyForm Noncommercial 1.0.0. | `LICENSE` | High |
| `LICENSE` is a pointer to the license URL, not the full text. PolyForm's *Notices* clause accepts "a copy of these terms or the URL for them", so the pointer satisfies it. | License §Notices | High |
| `LICENSE` contains no copyright line and no `Required Notice:` line, so recipients have no required attribution and the licensor is not named anywhere in the tree. | `LICENSE`, repository search | High |
| Every commit in the tree is authored by `nani1902`; there are no outside contributions and no contributor agreement. The owner is therefore, on this record, the only licensor and is free to grant additional licenses ("These terms do not ... prevent the licensor from granting licenses to anyone else"). | `git log`; license §No Other Rights | High for authorship-by-commit; moderate that commit authorship equals copyright ownership |
| Parts of the tree were produced with AI assistance. Where a jurisdiction denies copyright to material without sufficient human authorship (the US Copyright Office's current position), the noncommercial restriction may be unenforceable for those parts. This does not restrict the owner; it weakens exclusivity. | US Copyright Office guidance; commit contents | Moderate |

### 2.2 What PolyForm Noncommercial permits pilot users to do

Permitted purposes are "any noncommercial purpose", personal use without anticipated commercial application (a non-exhaustive safe harbor), and use by a charitable organization, educational institution, public research organization, public safety or health organization, environmental protection organization, or government institution, "regardless of the source of funding".

| Intended participant | Licensed under current terms? | Confidence |
|---|---|---|
| For-profit visa/immigration agency, admissions consultancy, relocation or HR firm using CHONK for client work | **No.** This is use for a commercial purpose. | High |
| Freelancer who charges applicants for document preparation | **No.** | High |
| University or school admissions office | **Yes** — "educational institution". | High |
| Government office or charity (for example a refugee-assistance NGO) | **Yes.** | High |
| An individual preparing their own application | Probably yes — ordinary meaning of "noncommercial"; the enumerated personal-use list does not name this case, but it is a safe harbor, not the definition. | Moderate |
| An employee of a for-profit firm using it at work "just to try it" | **No** — the use is for the firm. | Moderate–high |

**Consequence:** the product plan's initial recruiting hypothesis ("visa/admissions processing teams", [product requirements §2](../product/PRODUCT_REQUIREMENTS.md#2-users-and-assumptions-to-validate)) is mostly a population that **cannot legally use CHONK under the current license**. Interviews and observation of their *current* workflow do not use CHONK and are unaffected. Any hands-on pilot with a commercial participant needs a separate license from the owner.

### 2.3 Dependencies

| Component | License (as verified) | Distribution fact that matters |
|---|---|---|
| Ghostscript | GNU AGPL v3 or later (`ghostpdl/LICENSE`); Artifex also sells commercial licenses (well known; Artifex's licensing page was not reachable from this environment) | CHONK does **not** bundle it. `pdf_compressor.py:find_ghostscript` discovers `gs`/`gswin64c.exe`/`gswin32c.exe` on `PATH` and invokes it as a separate process with command-line arguments. That arms-length use is, in the FSF's own reading of the GPL family, communication between separate programs, not a combined work. Confidence: moderate–high. |
| pypdf 6.19.0 | BSD-3-Clause (`License-Expression`) | Include copyright + license text in a bundle. |
| pypdfium2 5.13.0 | Bindings: Apache-2.0 / BSD-3-Clause; docs CC-BY-4.0. PDFium: BSD-3-Clause. The linux_x64 wheel ships `BUILD_LICENSES/` for **abseil, agg23, fast_float, freetype, icu, lcms, libjpeg_turbo (IJG + BSD), libopenjpeg, libpng, libtiff, llvm-libc, pdfium-binaries, pdfium, simdutf, zlib**. The README warns some builds link libgcc. | Notices differ per platform wheel; collect from the exact wheels bundled. |
| Pillow 12.3.0 | **MIT-CMU** (`License-Expression`). The wheel's `LICENSE` also carries sections for AOM, Brotli, bzip2, dav1d, FreeType2, HarfBuzz, LCMS2, libavif, libjpeg, liblzma, libpng, libtiff, libwebp, libyuv, OpenJPEG, raqm, Tcl/Tk, libXau, libxcb, libXdmcp, zlib, and zstd. | `THIRD_PARTY_NOTICES.md` said "HPND"; corrected in this change to MIT-CMU (the historical PIL text, now declared under the SPDX identifier MIT-CMU). |
| FreeType (inside both Pillow and pypdfium2) | Dual: FreeType License (FTL) or GPLv2 | Choosing the FTL requires a **credit in the product documentation**: "Portions of this software are copyright © <year> The FreeType Project (www.freetype.org). All rights reserved." Choosing GPLv2 instead would be unworkable for a noncommercial-licensed bundle. So a bundle must carry the FTL credit. |
| PyInstaller 6.22.3 | GPL-2.0-or-later with a **bootloader exception** ("unlimited permission to link or embed compiled bootloader and related files into combinations with other programs, and to distribute those combinations without any restriction coming from the use of those files"); run-time hooks and `fake-modules` are Apache-2.0 | A PyInstaller bundle may be distributed under CHONK's own terms; include the Apache-2.0 text for the embedded run-time hooks. |
| CPython runtime and Tcl/Tk (embedded by PyInstaller) | PSF License; Tcl/Tk BSD-style | Include both notices in a bundle. Confidence: high (not re-fetched in this review). |

`requirements.txt` uses floor constraints only (`>=`). Notices therefore cannot be reproduced for "the" bundle today: two builds on different days can contain different library sets. Exact pins are a prerequisite for any distributed binary.

## 3. Options for the pilot license

The strongest argument against doing anything: the pilot is small, nobody will sue, just run it. Rejected — it would put the licensor in the position of inviting use its own public license forbids, it contaminates any later claim that the noncommercial restriction means something, and participants' own compliance people may reject undocumented use of unlicensed software on client files.

| Option | What it does | Cost / risk |
|---|---|---|
| **A. Keep PolyForm NC; grant written pilot evaluation licenses** | The owner signs a short separate license with each commercial participant: no fee, named organization, time-limited (pilot length + buffer), evaluation use on their own devices, no redistribution, no warranty, terminates automatically. Public license unchanged. | Requires a document per participant and ideally counsel review of the template. Reversible. Preserves every later commercial option. |
| B. Keep PolyForm NC; recruit only noncommercial users | Universities, NGOs, government offices, individuals | Legally clean, but biases the sample away from the hypothesized paying customer; weak evidence for a commercial decision. |
| C. Relicense to an OSI license (Apache-2.0 or MIT) | Anyone may use commercially | Irreversible for every version released that way. Removes the plausible paid desktop/team model the requirements keep open. Makes the Ghostscript question more visible, not less. |
| D. Relicense to PolyForm Small Business 1.0.0 | Free for organizations with fewer than 100 people and under USD 1,000,000 (2019, CPI-adjusted) revenue | This is almost exactly the hypothesized customer ("small application-processing teams"). It would give away the target segment for free before learning whether it would pay. |
| E. Relicense to a delayed-open license (e.g. FSL/BUSL) | Commercial restriction for a period, then permissive | A change of license model decided without pilot evidence — exactly what requirements §9 says not to do. |

## 4. Proposed decision

1. **No relicensing.** CHONK remains PolyForm Noncommercial 1.0.0. Any change is a separate, later decision record, made after pilot evidence exists (requirements §9).
2. **Pilot eligibility.** Noncommercial participants may use CHONK under the current terms. A commercial participant may run CHONK only after signing a written evaluation license with the owner (option A). Discovery interviews and observation of existing workflows need no license and may proceed now.
3. **Ghostscript.** The pilot and v1 do not bundle Ghostscript; users install it from Artifex's official distribution and CHONK discovers it. Bundling is re-decided in CHONK-024 among: an AGPL-conformant aggregate (AGPL §5 "aggregate" clause, with corresponding-source offer and no restriction on replacing `gs`), an Artifex commercial license, or an alternate backend (CHONK-031). Install friction from this choice is measured in discovery (see [0004](0004-customer-hypothesis-and-go-no-go.md)).
4. **Bundle prerequisites (CHONK-024).** Before distributing any binary: pin exact dependency versions per platform; generate notices from the exact wheels bundled; include CPython, Tcl/Tk, PyInstaller Apache-2.0 run-time hooks, all `BUILD_LICENSES/` files, Pillow's full `LICENSE`, and the FreeType FTL documentation credit.
5. **Owner housekeeping (recommended, not required).** Add a line such as `Required Notice: Copyright <legal name of licensor>` to `LICENSE` so recipients must preserve attribution, and include the full license text rather than only the URL. Both are the owner's call because they name the legal licensor; neither changes license terms.

## 5. Evaluation-license key terms (for counsel to draft from)

Licensee (named organization) · purpose limited to evaluating CHONK for its own document preparation during the pilot · term ends on a fixed date · no fee · no redistribution or sublicensing · documents stay on licensee devices; licensor receives only content-free feedback · no warranty, liability cap · either party may terminate on notice · feedback may be used by licensor · no implied rights to future versions or pricing.

## 6. Sign-off

| Item | Owner decision | Date | Reference |
|---|---|---|---|
| Keep PolyForm NC 1.0.0 (no relicensing now) | _pending_ | | |
| Commercial pilot users require written evaluation license | _pending_ | | |
| No Ghostscript bundling for pilot/v1 | _pending_ | | |
| Bundle prerequisites in §4.4 | _pending_ | | |
