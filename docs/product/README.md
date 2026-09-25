# CHONK development plan

**Direction:** private PDF preparation for people and agents, starting with compression to an exact size limit.

**Product promise:** Give your agent the PDF task. Keep the document private. See what changed.

This is a proposed development specification, not a statement that the features are implemented. The repository owner can use the backlog to assign implementation work. No application behavior changes are introduced by these documents.

## Reading order

1. [Product requirements](PRODUCT_REQUIREMENTS.md) — users, problem, scope, requirements, and the intended experience.
2. [Architecture and migration](ARCHITECTURE.md) — current implementation, required changes, privacy boundary, interfaces, and failure behavior.
3. [Implementation backlog](TASKS.md) — ordered tasks, dependencies, deliverables, and acceptance criteria.
4. [Validation and release plan](VALIDATION.md) — fixtures, quality evaluation, privacy checks, pilot, and release gates.
5. [Supported-feature matrix](SUPPORTED_FEATURES.md) — what preflight inspection detects and what each policy blocks (implemented).

## Baseline and delivery boundaries

- Written: 2026-09-25.
- Source reviewed: [`82ab79e9870ecb6246f88a327843ad97ab4a6193`](https://github.com/nani1902/CHONK/tree/82ab79e9870ecb6246f88a327843ad97ab4a6193).
- Existing product: Python CLI and Tkinter desktop compressor using Ghostscript, pypdf, PDFium, and Pillow.
- First development milestone: reusable engine, preflight inspection, explicit results, and regression coverage.
- Public v1 target: packaged local application, batch preparation, local review, structured CLI, and local MCP integration using the same engine.
- Deferred: general PDF editing, automatic redaction, cloud processing, portal submission, and team administration.

## Decisions proposed by this plan

| Decision | Proposed default | Revisit when |
|---|---|---|
| First job | Prepare unsigned PDFs for a file-size-limited upload | Pilot shows a different repeated job dominates |
| First users | Small application-processing teams; individual users remain supported | Five-user discovery and pilot are complete |
| Execution | Local worker; no document-processing cloud fallback | A separate deployment model has its own privacy specification |
| Agent integration | Local MCP over stdio plus structured CLI | A real remote integration requires another transport |
| UI | Improve the existing Tkinter app before considering a rewrite | Usability testing shows an unmet requirement |
| Platform sequence | macOS pilot; Windows before broad v1 release; Linux source CLI initially | User recruitment or packaging evidence favors another order |
| Unsupported features | Explicitly block v1 processing of signed, encrypted, interactive-form, and other unvalidated document classes | Preservation support is implemented and tested |
| Quality claim | Passed named checks; uncertain cases need review | Evidence supports additional narrowly worded claims |
| Commercial model | Undecided; validate repeat usage and willingness to pay | Pilot and dependency-license review are complete |

These are planning defaults, not validated market findings. Platform order and commercial licensing are explicit decision gates in the backlog.

## First assignment

Start with **CHONK-001 through CHONK-004** in [TASKS.md](TASKS.md). Establish the synthetic regression corpus, decision record, shared engine, and result contract before adding MCP or expanding PDF operations.

## Definition of the end state

A person or authorized agent can request a batch of PDFs under an exact byte ceiling; CHONK either produces verified outputs with a clear local report, requests review of specific uncertainties, or returns an actionable failure. It retains originals, enforces the configured local processing boundary, and never silently relaxes requested constraints.
