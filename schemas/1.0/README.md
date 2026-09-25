# CHONK contract, schema version 1.0

This is the versioned request/result contract for future machine callers (JSON
CLI and local MCP). The Python definitions are the source of truth:
`src/chonk/models.py` (types), `src/chonk/policies.py` (policies), and
`src/chonk/contract.py` (parsing and semantic validation). The JSON Schemas here
are generated from them by `python scripts/generate_schemas.py`. A test fails
if the files drift from the code.

**Status:** defined and tested, but not yet produced by the engine or CLI.
Inspection (CHONK-005), validation (CHONK-008/009), and the JSON CLI (CHONK-016)
will emit these results.

| File | Describes |
|---|---|
| `prepare-request.schema.json` | One file to prepare under an exact byte ceiling |
| `result.schema.json` | Content-free outcome for one file |
| `error.schema.json` | A rejected request or payload |
| `examples/` | Synthetic examples; each status and completion basis has at least one |

The JSON Schemas check shape, enums, and numeric limits. Some rules depend on
the policy, so JSON Schema cannot express them. `chonk.contract` enforces those
rules, so a document can pass the schema and still be rejected.

## Versions

- `schema_version` must be `"1.0"`. Any other value is rejected with
  `UNSUPPORTED_SCHEMA_VERSION`. The version is checked before any other
  field, so a request written for a newer schema is reported as a version
  mismatch, not as a list of unknown fields.
- A policy ID has the form `<name>-v<version>`. A known name with another
  version returns `UNSUPPORTED_POLICY_VERSION`. An unknown name returns
  `UNKNOWN_POLICY`. A published policy version never changes meaning. A
  stricter or looser rule gets a new version. The v1 definitions stay
  provisional until the first release.

## Request limits

| Field | Rule |
|---|---|
| `target_bytes` | Integer from 1 to 10,737,418,240 (10 GiB). Booleans, floats (including `2000000.0`), and strings are rejected |
| `deadline_seconds` | Integer from 1 to 86,400; required |
| `max_attempts` | Integer from 2 to 64; default 16 |
| `file_id`, `job_id`, `artifact_id` | Opaque handles: 1–128 characters, `[A-Za-z0-9][A-Za-z0-9_-]*`. They can never be paths |
| `idempotency_key` | Optional; 1–128 characters, `[A-Za-z0-9][A-Za-z0-9._:-]*` |

Unknown fields are rejected. Error messages name the field and the limit, but
never repeat the submitted value or an unrecognized field name, because either
can be sensitive.

## Policies

| Check | `preserve-existing-text-v1` | `require-searchable-v1` | `scan-review-v1` |
|---|---|---|---|
| `size_ceiling` | required | required | required |
| `feature_support` | required | required | required |
| `page_structure` | required | required | required |
| `text_layer` | — | required | — |
| `extracted_text` | required; `not_applicable` allowed | required | advisory; `not_applicable` allowed |
| `visual_policy` | advisory | advisory | advisory |
| Pages without text | review | block (`TEXT_LAYER_MISSING`) | review |

In v1, every policy blocks these features: encryption, digital signatures,
interactive and XFA forms, active content, embedded files, annotations, links,
outlines, and tagged structure. A feature blocks until a validator exists that
can prove it is preserved. A feature that inspection did not report, or could
not decide, counts as `unknown` (`INSPECTION_INCONCLUSIVE`). It never counts
as absent.

`scan-review-v1` makes `extracted_text` advisory. The architecture lists only
structural checks as required for scans. A change to an existing OCR layer
therefore goes to review under that policy, but is a hard failure under
`preserve-existing-text-v1`.

## Result rules

- **`ready`** needs every policy check reported, every required check `pass`
  (or an allowed `not_applicable`), an `artifact_id`, `output_bytes` at or
  below the target, and a `completion_basis`:
  - `automated_checks` or `unchanged_source`: every advisory check is also
    closed.
  - `human_review`: `review.accepted_checks` names exactly the open advisory
    checks. Check states stay as they were, and the acceptance is recorded
    beside them. A required check can never be accepted, so review cannot
    override a size, structure, text, or feature failure.
- **`needs_review`** has the same hard-check rules as `ready`, at least one
  open advisory check, `output_bytes`, and no `artifact_id`. The pending
  output is local-only. The reason codes must match the open checks.
- **`target_not_met`** has one reason, `TARGET_NOT_MET`, and search evidence
  whose `smallest_tested_bytes` is above the target. It covers the bounded
  search only; it does not prove that no smaller output exists.
- **`blocked`**, **`failed`**, and **`cancelled`** may report partial checks.
  An absent check means the check was not run; it never means `pass`.
  `PRESERVATION_CONSTRAINT_FAILED` requires a failed required preservation
  check.
- `size_ceiling` must agree with `output_bytes` whenever both are present.
- `next_action` is derived from the status and reasons (`next_action_for`),
  so clients never need to interpret prose.

| Status | Allowed reason codes | `next_action` |
|---|---|---|
| `ready` | none | `use_local_artifact` |
| `needs_review` | `QUALITY_REVIEW_REQUIRED`, `VALIDATION_INCONCLUSIVE` | `open_local_review` |
| `target_not_met` | `TARGET_NOT_MET` | `increase_target` |
| `blocked` | `ENCRYPTED_INPUT`, `SIGNED_INPUT`, `UNSUPPORTED_FEATURE`, `INSPECTION_INCONCLUSIVE`, `MALFORMED_INPUT`, `TEXT_LAYER_MISSING`, `SOURCE_CHANGED`, `ACCESS_DENIED`, `OUTPUT_CONFLICT`, `PRESERVATION_CONSTRAINT_FAILED`, `VALIDATION_INCONCLUSIVE`, `RESOURCE_LIMIT_EXCEEDED` | by reason |
| `failed` | `BACKEND_UNAVAILABLE`, `BACKEND_FAILED`, `INTERNAL_ERROR`, `RESOURCE_LIMIT_EXCEEDED` | by reason |
| `cancelled` | exactly one of `CANCELLED_BY_USER`, `DEADLINE_EXCEEDED`, `REVIEW_REJECTED`, `REVIEW_EXPIRED` | by reason |

When a blocked or failed result has several reasons, `next_action` takes the
first action in this order: `request_access`, `resolve_output_conflict`,
`retry`, `check_installation`, `handle_manually`, `report_failure`.

Results contain no paths, file names, document text, thumbnails, or hashes. The
result schema's field list is pinned by a test, so adding a field is a
deliberate change.
