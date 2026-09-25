# 0004 — Customer hypothesis and discovery go/no-go criteria

- Status: **Proposed — outcome pending discovery.** Criteria are fixed before any session so that results cannot be read to fit a preferred conclusion.
- Task: CHONK-002. Gates investment beyond CHONK-001, -003, and -004 (those three improve the existing tool regardless of outcome).
- Date: 2026-09-25.

## 1. Hypothesis

**Primary:** Staff in small organizations (roughly 2–20 people) that prepare application packets for other people — visa/immigration, study-abroad and admissions services — upload PDFs to size-limited portals every week. Fitting files under the limit costs them measurable time and retries, and client-confidentiality expectations make them reluctant to use online compressors. They would install a local desktop tool that fits files to an exact limit and says when it cannot do so safely.

**Secondary:** Individuals preparing their own applications have the same problem occasionally; they value privacy but tolerate little installation effort.

**Agent workflow:** Developers building document-handling agents want a local tool with typed results rather than scripting Ghostscript. Five operator interviews cannot test this; it needs at least one developer session and is otherwise deferred to M4 evidence.

## 2. Strongest reasons the hypothesis may be wrong

Stated up front so the sessions probe them instead of avoiding them:

1. **The problem is already solved well enough.** Offices that own Acrobat have "Reduce File Size"; macOS Preview exports with a Quartz filter; Stirling-PDF offers a target size. If participants use one of these and rarely retry, the pain is small.
2. **The privacy constraint is stated but not practiced.** People who say client files are confidential may still use free online compressors daily. Only observed or described behavior counts, not stated attitudes.
3. **The real bottleneck is elsewhere.** Bad phone scans, merging many files into one upload, or portal-specific naming rules may dominate. CHONK v1 does none of these.
4. **Licensing blocks the segment.** Commercial participants cannot use CHONK under its current license without a separate grant ([0001](0001-licensing-and-distribution.md)). If none will sign an evaluation license, the pilot cannot happen with this segment, whatever the interviews say.
5. **Install tolerance is too low.** Managed office machines, or users unwilling to install Ghostscript, can defeat a local tool whatever its merits.

## 3. Recruiting quota

Five sessions: three operators from primary-segment organizations (no two from the same organization), one individual applicant, and one agent or application developer. Screen with the [interview guide](../discovery/INTERVIEW_GUIDE.md) §A. Record every screened-out candidate's reason in the synthesis, without identity, so selection bias is visible.

## 4. Criteria

A participant is a **qualifying user** when their record ([template](../discovery/RECORD_TEMPLATE.md)) shows all of:

- **Frequency:** performs size-limited PDF preparation at least weekly (`B1`).
- **Pain:** at least one retry, portal rejection, or quality complaint in a typical week, **or** at least 10 minutes per week spent fitting files to limits (`C1`–`C4`). Observed baseline time takes precedence over estimates.
- **Sensitivity:** an observed or described practice (not only an opinion) that restricts where documents may be processed — a policy, a client promise, or refusal to use online tools (`E1`–`E3`).
- **Install tolerance:** can install a desktop application and Ghostscript, or would get IT approval within the pilot window (`F2`–`F4`).

Among the four non-developer sessions:

| Outcome | Condition | Action |
|---|---|---|
| **Go** | ≥ 3 qualifying users, **and** ≥ 1 commercial or noncommercial participant willing to pilot under the terms in 0001 | Proceed with M1–M2 as planned; select pilot OS per [0003](0003-pilot-platform.md). |
| **Iterate** | 1–2 qualifying users, **or** ≥ 3 users share a pain but a different job dominates (§2.3) | Re-scope the first job before M2; interview five more in the adjusted segment; CHONK-001/-003/-004 continue. |
| **Stop** | 0 qualifying users, **or** all participants are satisfied with an existing tool and report no retries | Stop product expansion; maintain CHONK as a free noncommercial utility. |

Five sessions are directional evidence. They cannot establish market size, willingness to pay, or statistical significance, and the synthesis must say so.

## 5. Outcome (filled after discovery)

| Field | Value |
|---|---|
| Sessions completed | 0 of 5 |
| Qualifying users | _pending_ |
| Willing pilot participants (and license route) | _pending_ |
| Decision | _pending_ |

## 6. Sign-off

| Item | Owner decision | Date | Reference |
|---|---|---|---|
| Criteria §4 fixed before sessions | _pending_ | | |
| Outcome §5 | _pending discovery_ | | |
