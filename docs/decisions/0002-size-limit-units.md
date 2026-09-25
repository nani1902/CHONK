# 0002 — Interpreting a size limit typed by a user

- Status: **Proposed.** Discovery must confirm or refute the portal-convention assumption in §3 (interview questions in [the guide](../discovery/INTERVIEW_GUIDE.md), section D).
- Task: CHONK-002. Consumed by CHONK-001 (baseline tests), CHONK-004 (request validation), CHONK-017 (desktop UX).
- Date: 2026-09-25. Reviewed tree: `6509a56`.

## 1. Question

When a person reads "Max 2 MB" on an upload portal and types `2 MB` into CHONK, which byte count should CHONK enforce, and how should it be shown?

## 2. Baseline behavior (verified by running `pdf_compressor.parse_size` at `6509a56`)

| Input | Parsed bytes | Displayed by `human_size` | Assessment |
|---|---|---|---|
| `2MB` | 2,000,000 | `1.91 MiB (2,000,000 bytes)` | Correct decimal interpretation; display switches to binary units the user never typed. |
| `2MiB` | 2,097,152 | `2.00 MiB (2,097,152 bytes)` | Correct. |
| `4.1MB` | **4,099,999** | `3.91 MiB (4,099,999 bytes)` | Off by one byte: `int(4.1 * 1_000_000)` truncates a binary-float product. Errs small, so never unsafe, but the ceiling is not the one the user typed. |
| `2.5B` | **2** | `2 bytes` | Fractional bytes silently truncated instead of rejected. |
| `2 Mb` | 2,000,000 | — | Unit matching is case-insensitive, so `Mb` (conventionally megabit, 125,000 bytes) is read as megabyte. |

## 3. Analysis

**Risk is asymmetric.** A portal that says "2 MB" enforces either 2,000,000 bytes (decimal) or 2,097,152 bytes (binary). CHONK cannot know which.

- If CHONK assumes decimal and the portal is binary, the output is 97,152 bytes (4.63%) smaller than necessary. Cost: a slightly more compressed file.
- If CHONK assumes binary and the portal is decimal, a 2,050,000-byte output is **rejected by the portal**. Cost: the exact failure CHONK exists to prevent.

Headroom given up by the decimal reading: 2.34% at K, 4.63% at M, 6.87% at G. At the megabyte scale that matters for portals, 4.63% of a ceiling is almost never the difference between a readable and an unreadable result. (Moderate confidence; the quality corpus in CHONK-009 can measure it.)

**Operating systems disagree with each other**, which is what makes the user's own check unreliable. Windows File Explorer labels binary multiples as "KB"/"MB"; macOS Finder has used decimal units since Mac OS X 10.6. A 2,080,000-byte file therefore reads as about "1.98 MB" in Explorer and "2.1 MB" in Finder. A Windows user who "checks" a file against a decimal portal limit can be misled by up to 4.63%. (High confidence on the OS conventions; the magnitude of real-world confusion is unknown and is an interview question.)

**Unknowns that the analysis cannot settle** (discovery questions, not assumptions): whether any target portal counts a limit across all attachments combined; whether a portal's limit is inclusive (`≤`) or strict (`<`); whether portals enforce the limit on the raw file or on an encoded form (for example Base64 in a JSON API is ≈4/3 larger); and whether users get an explicit number or only an error after upload.

## 4. Proposed decision

1. `KB`, `MB`, `GB` are decimal (10³, 10⁶, 10⁹); `KiB`, `MiB`, `GiB` are binary. This keeps the baseline and README behavior.
2. The ceiling is inclusive: an output of exactly the target byte count satisfies it. If discovery finds a strict-`<` portal, the user enters one byte less; CHONK does not guess.
3. The UI and CLI show the enforced integer byte count next to the unit the user typed (for example `2 MB = 2,000,000 bytes`), never only a converted unit the user did not type. Binary equivalents may appear as secondary information.
4. Parsing is exact: convert with decimal arithmetic (for example `decimal.Decimal`) rather than binary floats. Reject non-integer byte results (`2.5B`, `0.0001KB`) rather than truncating. Reject an empty or zero result. Bound the maximum accepted value (CHONK-004 sets the number).
5. Unit matching distinguishes bits from bytes: accept `B`/`KB`/`MB`/`GB`/`KiB`/`MiB`/`GiB` and case variants that cannot be read as bits (`kb`, `mb`, `gb` remain accepted as bytes, matching common portal spelling), but reject `Mb`, `Kb`, `Gb`, `Mbit`, `bit` with a message explaining the bit/byte difference. Whether `mb` should also be rejected is a discovery question (does anyone type it?); default: accept.
6. Discovery records the literal limit text shown by each participant's portals (a number and unit, not the portal name if that identifies the participant) and whether any rejections occurred on files that "looked" under the limit.

## 5. Consequences

- CHONK-001 fixtures assert the current truncation behavior as a baseline characterization; CHONK-004 changes it and documents the change.
- A separate backlog suggestion has been filed for the parser defects in §2; they are not fixed in this decision-only change.
- If discovery finds that most participants' portals are binary and outputs are needlessly over-compressed, revisit item 1 by offering a per-destination preset, not by changing what `MB` means.

## 6. Sign-off

| Item | Owner decision | Date | Reference |
|---|---|---|---|
| Items 1–5 | _pending_ | | |
