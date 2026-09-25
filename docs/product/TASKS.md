# Implementation backlog

All tasks below are **not started** unless their entry states otherwise. The documents are specifications; no runtime feature should be considered delivered because it appears here. These task descriptions can be copied into GitHub issues without further decomposition for initial assignment. Creating this file does not create issues or assign people.

References: [requirements](PRODUCT_REQUIREMENTS.md), [architecture](ARCHITECTURE.md), [release gates](VALIDATION.md).

## Delivery sequence and estimation

| Milestone | Outcome | Tasks | Exit gate |
|---|---|---|---|
| M0 — Baseline and decisions | Grounded scope, reproducible fixtures, licensing/platform decision | 001–002 | Baseline and decision record reviewed |
| M1 — Reliable engine | Shared engine, inspection, constraints, typed outcomes | 003–007 | No UI/log coupling; unsupported cases blocked |
| M2 — Verified execution | Page/text validation, bounded search, isolation, receipts | 008–015 | Quality and privacy test suites pass |
| M3 — Human workflow | JSON CLI, review, batch UX | 016–020 | A person can finish or resolve every supported outcome |
| M4 — Agent workflow | Scoped local MCP with repeatable behavior | 021–023 | Agent completes without reading PDF contents |
| M5 — Distribution and pilot | Packages, documentation, measured pilot, release | 024–027 | All v1 gates pass |

Priority: **P0** blocks trustworthy core behavior or privacy; **P1** is required for public v1 experience/distribution; **P2** is later work and excluded from the committed scope. Size is a planning estimate: S = 1–2 engineering days, M = 3–5, L = 6–10. Estimates exclude external review, waiting, and major research surprises. Re-estimate after M0; this is not a calendar commitment. Isolation, quality calibration, and packaging have the most uncertainty.

Owner fields name a responsibility, not a person. One developer may fill several roles. Work may overlap only when dependencies and interface decisions permit it. Do not begin MCP before authorization, isolation, and response filtering exist.

## M0 — Establish evidence

### CHONK-001 — Baseline tests and synthetic PDF corpus

- Status: **complete**. The baseline suite, synthetic corpus, and recorded baseline defects are in [`tests/`](../../tests/README.md).
- Priority / size / owner: P0 / M / engine + QA.
- Dependencies: none.
- Files: new `tests/`, fixture generators, test configuration; exercise existing `pdf_compressor.py`.
- Work: generate text, image-only, mixed, tiny-text, multi-page, encrypted, form, annotation, signature-bearing, malformed, and already-small PDFs. Use a dedicated test certificate for cryptographic-signature fixtures where needed. Record fixture intent and provenance. Capture existing size parsing, original protection, and output behavior.
- Acceptance:
  - Fixtures are reproducible and contain no real customer data.
  - Tests assert input bytes never change and current size ceilings use correct units.
  - Tests expose nonmonotonic candidate sizes, early endpoint failure, and the minimum-over-tested-candidates reporting case.
  - Deliberately damaged outputs demonstrate that the evaluation suite can detect its intended regressions.

### CHONK-002 — Validate initial job and resolve distribution decisions

- Priority / size / owner: P0 / M / product + release.
- Dependencies: none; complete before commercial pilot or bundle distribution.
- Files: new decision records under `docs/decisions/`; update product assumptions with evidence.
- Work: interview five likely users, observe current preparation steps, confirm platform order and size-limit conventions, decide pilot usage permissions, and review CHONK/dependency licensing for the intended distribution. Distinguish current license terms from any proposed change.
- Acceptance:
  - Record frequency, baseline time/retries, sensitive-file constraints, and install tolerance without customer document contents.
  - Document supported pilot OS, customer hypothesis, and go/no-go criteria.
  - Record an explicit licensing/distribution decision before using CHONK in a commercial pilot; do not silently relicense.

## M1 — Build a reliable core

### CHONK-003 — Extract a UI-independent engine

- Status: **implemented** in [`src/chonk/`](../../src/chonk/); legacy launchers remain.
- Priority / size / owner: P0 / M / engine.
- Dependencies: 001.
- Files: `src/chonk/engine.py`, backend/search modules, compatibility launchers, `pyproject.toml`.
- Work: move orchestration out of argparse and print statements; inject progress callbacks and backend configuration. Keep CLI and desktop entrypoints working.
- Acceptance:
  - Engine imports without Tkinter or MCP and accepts a typed request.
  - Existing supported compression scenarios retain target/original guarantees.
  - Engine returns data rather than requiring callers to scrape stdout/stderr.

### CHONK-004 — Define versioned results, errors, and policies

- Status: **implemented**: [`models.py`](../../src/chonk/models.py), [`policies.py`](../../src/chonk/policies.py), [`contract.py`](../../src/chonk/contract.py), and [`schemas/1.0`](../../schemas/1.0/README.md).
- Priority / size / owner: P0 / M / engine + integration.
- Dependencies: 003.
- Files: `models.py`, `policies.py`, contract schemas/tests.
- Work: implement request validation, result/check states, reason codes, completion basis, and versioned policy definitions from the architecture.
- Acceptance:
  - Serialize/validate examples for every result status and reject unknown schema/policy versions.
  - Required `unknown` checks cannot produce `ready`.
  - Human review cannot override a hard size or preservation failure.
  - Exact target bytes are positive integers; request validation has bounded numeric limits.

### CHONK-005 — Preflight feature inspection

- Status: **implemented** in [`inspection.py`](../../src/chonk/inspection.py), with the [supported-feature matrix](SUPPORTED_FEATURES.md). The engine blocks input before any backend runs. Page kinds are reported, not yet enforced: CHONK-007.
- Priority / size / owner: P0 / L / PDF engine.
- Dependencies: 001, 004.
- Files: `inspection.py`, fixtures, feature-policy tests.
- Work: inventory encryption, signatures, forms including XFA where detectable, annotations, links, outlines, attachments, active content, tags, page geometry, and text-layer coverage. Report inspection uncertainty explicitly.
- Acceptance:
  - Unsupported fixture classes are blocked before Ghostscript executes.
  - Unrecognized or failed feature inspection is not treated as feature absence.
  - Image-only, mixed, blank, and text-bearing pages are differentiated conservatively.
  - A documented supported-feature matrix controls transformations.

### CHONK-006 — File grants and race-safe publication

- Status: in progress. POSIX primitives and filesystem/authorization tests exist in `src/chonk/runtime/files.py` and `tests/security/test_files.py`. Not yet wired into any engine, job, CLI, or MCP path. Started before 004, so reason codes are defined locally (`FileReason`), including two proposed additions, `ARTIFACT_MISMATCH` and `PLATFORM_UNSUPPORTED`, for 004 to adopt or rename. Open items: Windows support (currently fails closed), running the tests on macOS (the `renameatx_np` path has not been exercised), and the owner-only workspace directory (013).
- Priority / size / owner: P0 / L / runtime + security.
- Dependencies: 004.
- Files: `runtime/files.py`, authorization and filesystem tests.
- Work: bind file/destination grants to local client sessions; snapshot stable input bytes; reject alias/path escapes; implement staged checked output with atomic publication and explicit overwrite policy.
- Acceptance:
  - Traversal, symlink/hard-link source aliases, changed inputs, revoked grants, cross-client handles, and unauthorized destinations are rejected.
  - A concurrently created output is not overwritten by a default no-clobber request.
  - Published bytes match the validated artifact; all original fixtures remain unchanged.
  - Retries cannot widen access scope.

### CHONK-007 — Policy enforcement and unchanged-source fast path

- Priority / size / owner: P0 / M / engine.
- Dependencies: 005, 006.
- Files: engine/policies and integration tests.
- Work: enforce feature restrictions and policy applicability; handle already-small supported inputs without rewriting.
- Acceptance:
  - An already-small eligible file produces byte-identical output and `unchanged_source` evidence.
  - Unsupported inputs remain blocked even if already below the limit.
  - Scans fail `require-searchable-v1` with an actionable reason.
  - No fallback silently removes features or changes policy.

## M2 — Verify output and contain execution

### CHONK-008 — Structural and existing-text validation

- Priority / size / owner: P0 / L / PDF engine.
- Dependencies: 005, 007.
- Files: `validation/structure.py`, `validation/text.py`.
- Work: compare parseability, count, dimensions/rotation, page correspondence, supported features, and per-page extracted text. Document conservative normalization and extraction limitations.
- Acceptance:
  - Missing/reordered pages, changed numbers, text-layer loss, geometry changes, and lost supported features are caught by targeted fixtures.
  - Empty text extraction never proves preservation; uncertainty produces review or a block under policy.
  - Text content and sensitive diffs remain in local evidence only.

### CHONK-009 — Page-level visual evidence and calibrated decisions

- Priority / size / owner: P0 / L / PDF engine + QA.
- Dependencies: 001, 004, 008.
- Files: `validation/visual.py`, labeled quality corpus/evaluation script.
- Work: retain per-page and meaningful-region comparisons, identify worst pages, compare small text/line art/barcodes where represented in the corpus, and calibrate thresholds against human labels. Start conservatively with review for unsupported visual cases.
- Acceptance:
  - A single damaged page cannot be hidden by high average similarity elsewhere.
  - White margins cannot dominate the only quality decision.
  - Record false-ready and unnecessary-review rates by fixture class on held-out examples.
  - Thresholds are versioned and documented; no claim of universal readability is made.

### CHONK-010 — Replace unsafe search assumptions

- Priority / size / owner: P0 / M / engine.
- Dependencies: 008, 009.
- Files: `search.py`, candidate selection tests.
- Work: use actual candidate sizes and checks, preserve a bounded exploration budget, avoid declaring failure solely from an endpoint, and retain all tested evidence.
- Acceptance:
  - Nonmonotonic mock/profile fixtures can discover a fitting candidate away from the presumed endpoints within the specified test budget.
  - Select only hard-check-passing candidates; rank by documented quality criteria.
  - Report true smallest tested size, attempts, and search exhaustion without claiming global impossibility.
  - A fast return is permitted only when its required validation has completed.

### CHONK-011 — Worker isolation and total resource budgets

- Priority / size / owner: P0 / L / runtime + security.
- Dependencies: 003, 004; platform decision from 002.
- Files: `runtime/worker.py`, platform sandbox adapters, resource tests.
- Work: contain inspection, rendering, extraction, and compression in killable worker processes. Enforce total wall time, memory, CPU/process count, rendered pixels, input/page limits, and scratch-disk budgets. Deny worker outbound network and unnecessary filesystem access.
- Acceptance:
  - A hanging renderer and a hanging compressor both terminate within the configured deadline plus documented shutdown grace.
  - Parent and descendant workers terminate on cancellation.
  - Egress attempts are denied in supported privacy mode and enforcement is measured on each release OS.
  - Missing enforcement causes an explicit unsupported privacy-mode result, not a success badge.
  - Oversized/adversarial fixtures cannot exhaust the host in the test environment.

### CHONK-012 — Job service, retries, and lifecycle

- Priority / size / owner: P0 / M / runtime.
- Dependencies: 006, 011.
- Files: `runtime/jobs.py`, lifecycle contract tests.
- Work: implement state transitions, session-scoped handles, cancellation, idempotency, artifact expiry, and restart recovery. Default to a bounded sequential queue before optimizing concurrency.
- Acceptance:
  - Identical repeated requests reuse the job; changed parameters with the same key return a conflict.
  - Jobs recover to a defined interrupted state after process restart.
  - Expired or unauthorized handles cannot retrieve artifacts.
  - Cancellation is idempotent and preserves unrelated jobs.

### CHONK-013 — Private workspace cleanup and retention

- Priority / size / owner: P0 / M / runtime + security.
- Dependencies: 006, 012.
- Files: workspace manager, cleanup/recovery tests.
- Work: create private job directories, track all derivatives, implement immediate scratch cleanup and review expiry, and validate cleanup manifests before deleting files.
- Acceptance:
  - Permissions/ACLs are checked on supported systems.
  - Success, errors, cancellation, expiry, and crash recovery remove the expected derivatives.
  - Tampered manifests and symlinks cannot redirect cleanup outside owned workspaces.
  - Documentation distinguishes ordinary deletion from guaranteed secure erasure.

### CHONK-014 — Local receipts and minimal agent responses

- Priority / size / owner: P0 / M / integration + security.
- Dependencies: 004, 008, 009, 012.
- Files: `runtime/receipts.py`, response allowlist and sentinel tests.
- Work: create complete local evidence and a separate versioned allowlisted response for agents. Scrub backend errors and logs.
- Acceptance:
  - Synthetic secret markers in text, filenames, metadata, paths, and diagnostics never appear in agent responses or default logs.
  - Agent responses omit content, thumbnails, names, full paths, and hashes by default.
  - Local receipts bind output to exact input snapshot, engine/policy versions, and check evidence.
  - No skipped or unsupported validation is labeled passed.

### CHONK-015 — Headless engine verification gate

- Priority / size / owner: P0 / M / QA.
- Dependencies: 007–014.
- Files: integration/security CI and evaluation reports.
- Work: run the matrix in `VALIDATION.md` against the integrated engine before adapter rollout.
- Acceptance:
  - Every defined terminal/review outcome has a fixture and deterministic expected reason.
  - No ready output exceeds its target, changes its original, or bypasses a hard constraint.
  - Runtime, leak, and isolation tests pass for the pilot platform.
  - Document unresolved limitations and block release on critical failures.

## M3 — Make the workflow usable by people

### CHONK-016 — Structured CLI with legacy compatibility

- Priority / size / owner: P1 / M / integration.
- Dependencies: 014, 015.
- Files: `adapters/cli.py`, `pdf_compressor.py`, CLI docs/tests.
- Work: implement prepare/inspect/status/cancel commands as appropriate, `--json`, separate progress, documented exit codes, and legacy argument mapping.
- Acceptance:
  - Machine stdout is valid final JSON with no human log contamination.
  - Legacy documented source command still works; stricter policy behavior is documented.
  - Noninteractive review-required jobs return actionable status without waiting for hidden input.

### CHONK-017 — Desktop job and outcome UX

- Priority / size / owner: P1 / M / desktop.
- Dependencies: 012, 014, 015.
- Files: `ui/app.py`, `app.py`.
- Work: replace global stream redirection with typed events; show preflight, exact interpreted bytes, phases, cancellation, and specific outcomes.
- Acceptance:
  - Backend missing, unsupported PDF, timeout, and target-not-met each show a distinct useful message.
  - UI remains responsive during processing and shutdown.
  - Keyboard operation and status text work without relying on color.

### CHONK-018 — Local comparison and review resolution

- Priority / size / owner: P1 / L / desktop + PDF engine.
- Dependencies: 009, 013, 014, 017.
- Files: `ui/review.py`, review lifecycle tests.
- Work: synchronized before/after pages, zoom, flagged-page navigation, explanation of uncertainty, accept/reject/discard, and expiry.
- Acceptance:
  - Only advisory uncertainty can be accepted; hard failures have no override control.
  - Review binds to the exact candidate and policy version; changed bytes invalidate the decision.
  - Acceptance produces `human_review` evidence while preserving original check states and limitations.
  - No previews or document text are sent to the agent to perform review.

### CHONK-019 — Batch queue and safe output naming

- Priority / size / owner: P1 / M / desktop + runtime.
- Dependencies: 012, 017, 018.
- Files: batch job models, UI queue, batch tests.
- Work: multiple files, per-file limits, independent results, cancellation, output naming, and summary.
- Acceptance:
  - One unsupported or failed file does not erase or block other completed results.
  - Duplicate basenames and retries cannot overwrite files unexpectedly.
  - Summary counts match per-file states; review files remain withheld.

### CHONK-020 — Human usability verification

- Priority / size / owner: P1 / S / product + QA.
- Dependencies: 016–019.
- Work: observe representative users completing success, review, and failure scenarios with synthetic files.
- Acceptance:
  - Users can explain whether the file is ready, why review is needed, and where their original/output reside.
  - Record setup friction, task time, and misleading messages; resolve blocking issues before pilot.

## M4 — Make the workflow usable by agents

### CHONK-021 — Scoped local MCP server

- Priority / size / owner: P1 / M / integration.
- Dependencies: 006, 011, 012, 014–016.
- Files: `adapters/mcp.py`, tool schemas, integration setup documentation.
- Work: implement the six tools in the architecture over local stdio and the shared job service. Use strict typed input and output schemas.
- Acceptance:
  - No unauthenticated network listener or arbitrary-path/file-read tool is introduced.
  - Tool calls are bound to local grants and use fixed backend configuration.
  - Capability reporting reflects actual policy and isolation support.
  - Unknown/malformed requests produce structured errors without raw diagnostics.

### CHONK-022 — Local grant setup and review handoff

- Priority / size / owner: P1 / M / desktop + integration.
- Dependencies: 018, 021.
- Files: local integration setup and grant UI.
- Work: let a person select input files/output destinations, revoke grants, and open local review from an agent request.
- Acceptance:
  - Once authorized, repeated in-scope operations do not need repeated permission dialogs.
  - Agent cannot approve its own uncertain output or expand file scope.
  - Revocation/expiry produces actionable errors and does not leak prior document details.

### CHONK-023 — Agent completion and misuse evaluation

- Priority / size / owner: P0 / M / QA + security.
- Dependencies: 019, 021, 022.
- Files: contract/integration/security tests and agent evaluation scripts.
- Work: test realistic multi-step calls in at least two selected MCP clients; include retries, cancellation, malformed parameters, prompt-like PDF content, and unauthorized handles.
- Acceptance:
  - Agent completes supported preparation using file handles and structured checks without seeing contents.
  - Mixed batches and review handoffs are correctly interpreted.
  - Malicious document content cannot change policy or cause command/file-access escalation.
  - All privacy sentinel tests pass at the complete tool boundary.

## M5 — Ship and validate demand

### CHONK-024 — Reproducible packages and dependency lifecycle

- Priority / size / owner: P1 / L / release.
- Dependencies: 002, 020, 023.
- Files: build scripts, pinned release dependencies, packaging CI, notices.
- Work: package pilot macOS build then Windows release; verify signing/notarization requirements, dependency discovery or licensed bundling strategy, notices, updates, and uninstall behavior. Keep Linux source CLI documented.
- Acceptance:
  - Clean-machine install reaches a first successful preparation without Python setup.
  - Required external dependencies are installed/discovered through a documented tested flow if not bundled.
  - Build versions and required notices are reproducible; no automatic document-session egress.
  - Isolation/privacy gates pass for every platform advertised as supported.

### CHONK-025 — User, developer, and privacy documentation

- Priority / size / owner: P1 / M / documentation + product.
- Dependencies: 020, 023, 024.
- Files: main README, usage, integration, supported-file, troubleshooting, and privacy documentation.
- Work: document installation, limits/units, CLI/MCP setup, policies, statuses, review, retention, grants, privacy boundaries, and recovery.
- Acceptance:
  - Examples run against the built release.
  - No proposed feature is described as shipped until verified.
  - Documentation avoids “guaranteed readability,” “secure deletion,” or compliance claims unsupported by tests.

### CHONK-026 — Two-week measured pilot

- Priority / size / owner: P1 / M engineering effort plus two elapsed weeks / product + QA.
- Dependencies: 002, 024, 025.
- Work: recruit five users, establish individual baseline workflows, measure time/retries/review burden and repeat use, and collect content-free feedback. Real PDFs remain on participant devices unless a separate explicit arrangement exists.
- Acceptance:
  - Evaluate the provisional targets in product requirements and report sample size/limitations.
  - Record every critical false-ready result; fix and add a synthetic reproduction before release.
  - Produce a go/iterate/stop decision based on reliability and repeated need, not download counts.

### CHONK-027 — Release readiness and measured limits

- Priority / size / owner: P0 / M / release + QA.
- Dependencies: 015, 020, 023–026.
- Work: run all release gates, publish supported workload limits and benchmark environment, verify upgrade/uninstall, and label the supported scope accurately.
- Acceptance:
  - Each gate in `VALIDATION.md` has attached evidence.
  - No unresolved critical integrity/privacy issue; known noncritical limitations are documented.
  - Benchmarks include cold/warm runs, hardware, file classes, page counts, bytes, memory, and validation time.
  - Release notes distinguish baseline features from new behavior and explain stricter rejection cases.

## Deferred work: do not start before pilot evidence

| ID | Priority | Candidate work | Entry condition |
|---|---|---|---|
| CHONK-028 | P2 | Add merge/split using the same constraint and receipt model | Repeated observed user need and an operation-specific preservation spec |
| CHONK-029 | P2 | Local password-protected input handling | Secure local credential flow, encryption preservation policy, and dedicated tests |
| CHONK-030 | P2 | Local OCR assistance for scan review | Evidence that review burden is the bottleneck; evaluate latency and error modes without cloud fallback |
| CHONK-031 | P2 | Alternate compression backend | Corpus demonstrates a measurable preservation, speed, or distribution advantage |
| CHONK-032 | P2 | Team/self-hosted management | Validated buyer need plus separate authentication, deployment, retention, and commercial specification |

## Definition of done for each implementation task

- Implemented behavior matches the linked requirements and shared contracts.
- Relevant regression, negative, and boundary cases pass; tests prove behavior rather than merely mirror implementation.
- No customer data, credentials, real sensitive filenames, or temporary artifacts committed.
- Documentation and examples match shipped behavior.
- Any new limitation is explicit; an incomplete required check never silently becomes `pass`.
- Dependent tasks are unblocked only when the acceptance criteria are met.
