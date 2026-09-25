# Validation, pilot, and release gates

This plan defines how the proposed product earns its readiness and privacy claims. It is not evidence that the current repository passes these checks. Task mapping: [TASKS.md](TASKS.md).

## 1. Test data policy

Use generated or appropriately licensed public fixtures with recorded provenance. Prefer synthetic names, addresses, identifiers, and statements. Never commit real applicant, banking, identity, health, or client documents, including screenshots or extracted text, into this public repository or its issues/CI artifacts.

Each fixture records: generator/source, intended feature, expected policy decision, expected check outcomes, and whether a reviewer labeled the output. Store cryptographic test keys only if clearly designated disposable fixture keys and never reused for anything outside tests.

Keep calibration and evaluation examples separate. A threshold tuned on one corpus must be tested on held-out examples. Report the corpus size and coverage; passing a finite set is not a universal guarantee.

## 2. Required matrix

| Family | Representative scenarios | Expected evidence |
|---|---|---|
| Exact size | `B`, `KB`, `MB`, `MiB`; exact ceiling; one byte over; invalid/huge values | Correct interpretation; no ready output over target |
| Ordinary text | Single/multi-page, different fonts, multilingual, tables, vector diagrams | Stable structure; conservative per-page text comparison |
| Scans | Image-only, mixed scan/text, rotated scans, blank pages, faint print | Correct applicability; no false text-preserved claim |
| Local detail | Small digits, footnotes, line art, barcode/QR examples, one damaged page amid many clean pages | Flag localized degradation despite high document average |
| Feature inventory | Encryption, signature, AcroForm/XFA, annotations, links, outlines, attachments, JavaScript/actions, tags | Supported/unsupported/unknown distinguished before rewrite |
| Structural damage | Dropped, duplicated, reordered, rotated, resized, or blanked output pages | Hard failure or explicit uncertainty under policy |
| Text damage | Changed digits, missing text layer, garbled Unicode, extraction failure | Never silently passes preservation |
| Search | Nonmonotonic size ladder, endpoint fails while interior fits, budget exhaustion, isolated candidate failure | Truthful best/smallest-tested result and bounded execution |
| Already fits | Supported original below limit, unsupported original below limit | Byte-preserving fast path only for eligible inputs |
| Filesystem | Same file, hard links, symlinks, traversal, destination collision, concurrent creation, source mutation | Original integrity, scoped access, atomic no-clobber output |
| Runtime | Missing backend, corrupt PDF, hung parser/render/compressor, disk full, large page dimensions, child process | Specific result and reason; bounded resources/termination |
| Lifecycle | Retry, changed request key, revoke grant, cancel, restart, pending review expiry | No duplicate mutation; correct authorization and cleanup |
| Human review | Accept advisory uncertainty, reject, try hard-failure override, change candidate after review | Bound decision; hard checks cannot be overridden |
| Agent boundary | Secret markers in names/text/metadata/errors; unauthorized IDs; prompt-like PDF text | Minimal responses, no instructions executed, no content leakage |
| Packaging | Fresh install, dependency missing, offline run, update/uninstall | Supported platform behavior matches documentation |

For scan fixtures, barcode decoding or local OCR can be an evaluation instrument without becoming a promised v1 runtime capability. Keep the distinction explicit.

## 3. Quality evaluation

### Labels and severity

- **Critical regression:** missing content/page, changed significant number, unreadable essential text, lost required text layer/feature, or a broken hard constraint.
- **Review-worthy difference:** plausible readability loss, ambiguous extraction, or a visible change whose acceptability depends on the user's purpose.
- **Acceptable difference:** a reviewer considers the output usable for the fixture's declared task and required checks pass.

Have reviewers inspect at realistic reading zoom and at sufficient detail for flagged regions. Include the entire document when labeling ground truth so the model does not learn only to flag preselected pages. Record disagreements; unresolved examples should not be automated as ready.

### Reported metrics

- Critical false-ready count and rate: critical regressions among outputs labeled ready.
- False-review burden: acceptable results unnecessarily sent to review, plus review time.
- Target attainment: share of supported requests with an acceptable fitting candidate within budget.
- Coverage: supported, blocked, inconclusive, reviewed, failed, and cancelled counts separately.
- Performance: total latency and breakdown for inspection, compression, rendering, validation, and output writing; peak RAM and scratch disk.

Do not improve target attainment by quietly excluding failures or lowering preservation requirements. Do not advertise a single “quality percentage” as certainty. State engine, policy, corpus, and threshold versions with evaluation results.

## 4. Privacy and execution verification

1. Instrument worker egress in the supported OS isolation environment. Attempt outbound access from a controlled test worker; demonstrate denial rather than relying only on observing no traffic in a normal run.
2. Place unique synthetic secret markers in document text, metadata, filenames, directory paths, and fake backend errors. Assert they are absent from agent responses, default logs, and CI artifacts.
3. Exercise stale/cross-session handles and path alias attacks. A handle must not function as an unscoped bearer permission.
4. Verify renderers and parsers have the same deadline/resource boundary as Ghostscript.
5. Interrupt the app at each processing stage and inspect workspace recovery and output integrity.
6. Verify review expiry and cleanup never traverse outside directories the application created and owns.
7. Test that PDF text resembling instructions cannot change execution parameters, grant new access, trigger network calls, or become a tool instruction.
8. Test offline operation from a clean install with dependencies present. Report any setup/update network requirements separately from processing.

Use ordinary deletion language for cleanup; do not claim secure erasure. The tested boundary covers CHONK's worker and responses, not an independently privileged host, unrelated application, or cloud agent client.

## 5. Contract and workflow verification

Validate requests/results against a single versioned schema shared by CLI and MCP. Contract tests cover every status/reason pair, unknown policy versions, invalid numbers, missing grants, expired artifacts, and retry conflicts.

Run a human and an agent through the same sample workflow:

1. Grant three synthetic documents and a destination.
2. Prepare one ordinary text file, one scan needing review, and one unsupported form.
3. Observe one ready, one needs-review, and one blocked result, with no batch-wide loss.
4. Complete the review in the local UI; verify the agent cannot approve it.
5. Export ready outputs, check byte ceilings and original hashes locally, and verify cleanup.
6. Retry the same request and confirm no extra processing or duplicate overwrite.

Select two real MCP clients during implementation and record their versions/configurations; tool transport compatibility alone does not establish that agents understand statuses correctly.

## 6. Pilot design

Recruit five representative users for two weeks after the core privacy/integrity gates pass. Begin with synthetic examples, then allow their own locally retained files under the agreed pilot/license scope.

For each participant, measure a few representative jobs in their existing workflow and in CHONK. Record end-to-end time, number of manual retries, install friction, review burden, and outcomes. Use comparable file/task classes; report hardware and workload differences rather than claiming a controlled benchmark when none exists.

Provisional success signals: zero known critical false-ready incidents, at least 30% lower median task time, fewer manual retries, and at least three of five participants returning voluntarily. These small-sample targets guide a product decision, not statistical or compliance claims.

Interview users after a review/failure as well as after success. Determine whether they understood the result and could finish the job. A high block rate might mean the initial scope is wrong; it is not justification to remove safeguards silently.

Any critical failure gets a synthetic reproduction, a fix, and regression coverage before release. If users do not return, revisit the target job before investing in more operations.

## 7. Release gates

| Gate | Must be true | Evidence owner |
|---|---|---|
| G1: Product scope | Supported files, policy limits, non-goals, and pilot/licensing decisions are documented | Product/release |
| G2: Integrity | Zero target overruns, original mutations, unauthorized overwrites, or known critical false-ready regressions in the release suite | Engine/QA |
| G3: Uncertainty | Unknown/unsupported required checks never yield ready; review cannot bypass hard checks | Engine/QA |
| G4: Privacy | Egress enforcement, response sentinels, scoped grants, and retention tests pass on each advertised OS | Security/runtime |
| G5: Resilience | Full-job deadlines, cancellation, resource caps, retries, and restart cleanup pass | Runtime/QA |
| G6: Usability | Single-file, mixed-batch, review, and specific failure recovery are usable and keyboard accessible | Desktop/product |
| G7: Agent usability | Two selected MCP clients complete supported jobs with minimal disclosure and correct failure handling | Integration/QA |
| G8: Distribution | Fresh installs, dependencies, notices, versions, and supported platform enforcement verified | Release |
| G9: Evidence | Corpus coverage, limitations, performance environment, and pilot outcomes recorded | QA/product |

Automatic readiness should remain disabled for any document class without adequate validation evidence. A narrower truthful release is preferable to a broader untested readiness claim.

## 8. Release evidence record

For each release retain: commit/tag; supported OS and hardware; Python/backend/library versions; policy/schema versions; fixture provenance/counts; quality evaluation; security test results; contract test results; clean-install results; pilot summary; known limitations; and go/no-go decision. Keep this evidence content-free or synthetic so it can be reviewed without exposing private documents.
