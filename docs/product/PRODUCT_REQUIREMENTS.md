# Product requirements: private PDF preparation

Status: proposed specification. Baseline and reading order: [README](README.md).

## 1. Problem and product thesis

People often need to fit a sensitive PDF under an upload limit. They must choose compression settings, retry, check whether small text survived, and decide whether to trust an online service. An agent can automate the commands but still needs answers to three questions:

1. Is this operation authorized for this file and supported by the engine?
2. Did the output meet the requested constraints?
3. What evidence or human review is necessary before the output is usable?

CHONK should make those answers part of the operation. The first product is a local PDF preparation workflow with an exact size target, preservation policies, and an understandable result. Compression remains its first and only transformation in v1.

**Job statement:** “Prepare these documents for an upload limit, retain the information I need, keep processing local, and tell me when you cannot do that reliably.”

The agent directs the work; it does not need document text or page images to request compression. The local worker parses the file and performs validation. The agent receives a deliberately limited status response.

## 2. Users and assumptions to validate

| User | Repeated need | Desired experience | Discovery question |
|---|---|---|---|
| Application-processing operator | Prepare client statements and scanned supporting documents for portals | Batch queue, clear exceptions, quick local review | How often do size limits cause retries or rejected submissions? |
| Individual applicant | Prepare occasional sensitive files | Install, select files, enter limit, save | Will installation friction outweigh the privacy benefit? |
| Agent/application developer | Delegate file preparation without custom shell scripts | Typed inputs, stable outcomes, deadlines, repeatable calls | What evidence is necessary before an agent can continue? |

Initial recruiting hypothesis: visa/admissions processing teams. This is not evidence of demand or a compliance claim. Most such teams are commercial, so under CHONK's current noncommercial license they may be interviewed but cannot run CHONK without a separate written license from the owner ([decision 0001](../decisions/0001-licensing-and-distribution.md#22-what-polyform-noncommercial-permits-pilot-users-to-do)). The hypothesis, its strongest counterarguments, and fixed go/no-go criteria are in [decision 0004](../decisions/0004-customer-hypothesis-and-go-no-go.md). Interview five potential users before committing to broader feature development. Never collect their client PDFs into the public repository or issue tracker.

## 3. Positioning and alternatives

Local compression, target-size search, and agent access are already available elsewhere. Stirling documents [expected output size](https://docs.stirlingpdf.com/Functionality/Compress/) and a [local/self-hosted MCP server](https://docs.stirlingpdf.com/Configuration/Automation/MCP%20Server/). Adobe offers [cloud PDF compression](https://developer.adobe.com/document-services/apis/pdf-services/compress-pdf/).

CHONK's proposed differentiator is the complete decision workflow: explicit constraints, checks with evidence, minimal disclosure to the agent, local review, and predictable failure. This differentiation is a hypothesis to benchmark, not a claim that competitors lack all of these capabilities.

Do not position the product as a universal “safe PDF” certificate. Passing the configured checks does not prove perfect readability, absence of malware, accessibility compliance, or acceptance by a particular portal.

## 4. Scope

### Public v1 includes

- Local compression of supported, unsigned, unencrypted, noninteractive PDFs.
- Explicit decimal/binary size units and an exact byte ceiling per document.
- Batch queue with separate results and review decisions for each file.
- Preflight inspection of unsupported or preservation-sensitive features.
- Structural checks, extracted-text comparison where applicable, and page-level visual checks.
- Human review for uncertain visual/text cases, with a local before/after viewer.
- Readable desktop results and a versioned machine-readable result contract.
- Local CLI and stdio MCP adapters over one engine.
- Restricted local file grants, bounded workers, cancellation, deadlines, and documented temporary-file lifecycle.
- Packaged application with dependency diagnostics and clear installation instructions.

### Deferred beyond v1

- Signing, preserving digital signatures through a rewrite, editing forms, or decrypting password-protected documents.
- Automatic redaction, metadata sanitization, PDF malware sanitization, translation, and document summarization.
- Merge, split, repair, conversion, OCR creation, and general PDF editing.
- Emailing, uploading, or submitting the output to a portal.
- Cloud document processing, remote HTTP MCP, shared workspaces, and organization administration.
- Automatic claims of PDF/A or PDF/UA compliance.

Future operations should reuse the same inspect → execute → verify → review architecture, but each needs its own preservation and validation specification.

## 5. User journeys

### A. Person preparing one file

1. Open CHONK, select a file, and enter the destination's limit, for example `2 MB`.
2. See the interpreted ceiling, `2,000,000 bytes`, and the configured preservation policy.
3. CHONK inspects the document. Unsupported features produce a specific explanation before any rewrite.
4. CHONK tries supported compression candidates within a visible time budget.
5. Receive one of: ready to save, review required, no acceptable result, blocked file, cancelled job, or execution failure.
6. For review, inspect flagged pages locally, at useful zoom, before explicitly accepting an advisory uncertainty.
7. Save a new file. The original remains intact. A receipt lists what was checked and what was not.

### B. Agent preparing a batch

1. A person grants the local integration access to selected files and an output destination. This establishes scope; repeating an authorized operation should not cause another permission prompt.
2. The agent receives opaque file handles and supported policy identifiers.
3. The agent requests preparation with exact target bytes and an explicit deadline. No document contents are included in the request.
4. The agent polls job status or receives local progress through its adapter.
5. CHONK returns per-file results without text, images, names, or absolute paths by default.
6. Ready artifacts remain local. Review cases open in CHONK's local UI. Failed files do not prevent unrelated files from completing.
7. Downstream upload, if a different application supports it, remains a separate authorized action.

### C. No acceptable candidate

If no tested candidate meets the size ceiling, return `target_not_met` with the smallest tested size and search-budget information. If a candidate fits but violates a hard preservation constraint, return `blocked` with `PRESERVATION_CONSTRAINT_FAILED`. If checks are inconclusive and the policy allows human resolution, return `needs_review`.

Never reduce the target's quality policy, flatten a form, drop pages, or remove a signature to manufacture success. Never claim that a bounded unsuccessful search proves compression is impossible.

## 6. Functional requirements

| ID | Requirement | Observable completion condition |
|---|---|---|
| FR-01 | Exact size target | Every exported ready file is at or below the requested positive integer byte ceiling. Decimal and binary units display distinctly. |
| FR-02 | Original protection | Input bytes remain unchanged in success, failure, cancellation, and retry scenarios. |
| FR-03 | Preflight | Detect and report encryption, signatures, interactive forms, annotations, attachments, active content, and relevant structural features before transformation. Unknown inspection results cannot pass a required check. |
| FR-04 | Supported-file policy | v1 blocks encrypted and signed PDFs, interactive forms, active content, attachments, and features whose requested preservation is unsupported. Tagged/accessibility-dependent PDFs are blocked under the strict v1 policy until preservation is supported. |
| FR-05 | Preservation policy | Named, versioned policies identify required checks, advisory checks, and unsupported features. Agents cannot weaken a policy implicitly. |
| FR-06 | Candidate selection | Reject hard-check failures; compare eligible candidates using page-level evidence. Never choose only by whole-document average similarity. |
| FR-07 | Searchable-text check | For supported text-bearing files, compare normalized extraction per page and report differences/uncertainty. Do not expose extracted text to agent responses. |
| FR-08 | Scan handling | Image-only PDFs are explicitly marked as lacking an existing text layer. A require-searchable policy blocks them; a scan policy can prepare them without claiming text preservation or creating OCR. |
| FR-09 | Review | Show source/output at synchronized zoom with flagged pages and reasons. Only advisory or inconclusive checks may be accepted by a person. |
| FR-10 | Structured outcomes | CLI, desktop, and MCP agree on status, reason code, checks, target, artifact identity, and next action. |
| FR-11 | Batches | Queue files with independent targets and outcomes; preserve partial success and prevent output-name collisions. |
| FR-12 | Cancellation and deadlines | Stop worker process trees, bound rendering as well as compression, clean abandoned work, and report terminal state. |
| FR-13 | Already fits | After preflight, reuse the original bytes as a verified unchanged result when all requested conditions are met. Do not rewrite unnecessarily. Unsupported v1 inputs remain blocked. |
| FR-14 | Output publication | Stage, validate, and atomically publish the exact checked bytes; do not overwrite without an explicit local policy permitting it. |
| FR-15 | Integration | Headless operations require no GUI and do not parse human log strings. Tool descriptions explain limits and recovery actions. |
| FR-16 | Evidence | Local receipts record input/output hashes, engine/policy versions, checks, and review basis. Agent receipts expose only the allowlisted subset. |

“Strict” does not mean a mathematical proof of all document fidelity. It identifies the supported features and checks that must pass for this release.

## 7. Privacy and usability requirements

- Document-processing workers cannot make outbound network connections in the supported privacy mode. Test enforcement on each supported OS; a configuration string is not evidence of enforcement.
- The local model/agent client may itself communicate with a cloud model. CHONK minimizes what it returns; it cannot control unrelated host or agent behavior.
- Agent access is scoped to granted files and local destinations. A cloud agent must not gain arbitrary local filesystem access through CHONK.
- No raw PDF text, thumbnails, passwords, filenames, paths, or subprocess diagnostics in default tool responses or telemetry.
- No telemetry by default. Any future opt-in metrics must be separately specified, content-free, and reviewable.
- Show plain-language outcomes rather than presenting a similarity percentage as a safety guarantee.
- Use keyboard-accessible controls and text labels for statuses; never rely on color alone.
- Show real phases and counts. Do not invent percent complete for an unpredictable search.
- No LLM or network service is required to run compression or validation.

## 8. Product evidence and success metrics

Measure locally in the pilot with explicit consent; use synthetic files in CI.

| Metric | Measurement | Proposed pilot/release target |
|---|---|---|
| False-ready result | Marked ready, but a reviewer finds a critical regression | Zero known critical false-ready cases in the release corpus; publish corpus size and limitations |
| Target compliance | Ready/exported output exceeds byte ceiling | Zero |
| Original integrity | Source changed or overwritten | Zero |
| Privacy boundary | Undeclared network egress or content exposed in agent responses | Zero in the tested boundary cases |
| End-to-end time | From file selection to acceptable saved output | At least 30% lower median than each participant's existing workflow; provisional pilot target |
| Manual retries | User changes settings and reruns | Fewer than baseline; record reasons, not just aggregate counts |
| Review burden | Share of supported files needing review and time per review | Establish baseline first; do not lower checks to improve the number |
| Repeat use | Participants voluntarily return during a two-week pilot | At least 3 of 5; directional evidence, not statistical validation |

If repeat use is weak, revisit the job and target users before adding more PDF operations. If quality cannot be checked reliably enough for autonomous completion, retain a review-first product rather than weakening the promise.

## 9. Commercial and distribution decisions

Do not set pricing from this specification. Test whether users value individual convenience, repeated batch work, or integration into a business workflow. A plausible later model is a paid desktop/team offering, but it depends on demand and distribution costs.

The baseline source uses a noncommercial license and invokes separately installed Ghostscript. Before recruiting commercial users or distributing bundles, explicitly resolve the intended CHONK usage license and review dependency/distribution obligations. Do not change licenses as a side effect of implementing these requirements. See the repository's [license](../../LICENSE) and [third-party notices](../../THIRD_PARTY_NOTICES.md).

The license and dependency review for CHONK-002 is [decision 0001](../decisions/0001-licensing-and-distribution.md) (proposed; owner sign-off pending). Size-unit interpretation is [decision 0002](../decisions/0002-size-limit-units.md).
