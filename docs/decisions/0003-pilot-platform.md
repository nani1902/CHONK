# 0003 — Pilot operating system and supported-OS order

- Status: **Proposed — outcome pending discovery.** This record replaces the unvalidated "macOS pilot first" default with a selection rule. The rule is decided here; the OS it selects is filled in after five sessions.
- Task: CHONK-002. Consumed by CHONK-011 (isolation adapters), CHONK-024 (packaging), CHONK-026 (pilot).
- Date: 2026-09-25.

## 1. Why the current default is weak

The development plan proposes a macOS pilot, then Windows, with Linux as a source CLI ([plan README](../product/README.md#decisions-proposed-by-this-plan)). No evidence was recorded for that order. The strongest arguments against it:

1. **Install friction lands on the wrong platform.** CHONK does not bundle Ghostscript ([0001](0001-licensing-and-distribution.md) §4.3). Artifex publishes Windows installers; for macOS the usual routes are Homebrew or MacPorts, which a non-technical office operator is unlikely to have. A macOS pilot therefore either demands a developer tool from the least technical users or forces the Ghostscript bundling decision early. (Confidence: moderate–high on Artifex's download offerings as last known; Artifex's site was unreachable from this environment, so re-check before relying on it.)
2. **The target segment's machines are unknown.** Small application-processing offices are not known to be Mac-heavy. Nothing in the repository supports choosing macOS for them. (Confidence that the default is unsupported: high. Confidence about what they actually use: unknown — that is what the interviews measure.)
3. **Distribution trust costs exist on both sides.** A Gatekeeper-acceptable macOS app needs Developer ID signing and notarization through the paid Apple Developer Program; an unsigned Windows installer triggers SmartScreen warnings until it is signed and gains reputation. Neither platform is free of this; it is not a tiebreaker by itself.

The argument for macOS first: an unprivileged per-process network deny (`sandbox-exec` with a deny-network profile) is available today without administrator rights, which helps CHONK-011's egress gate. Apple documents `sandbox-exec` as deprecated, so this is a working mechanism, not a stable platform commitment. Windows has an equivalent in principle (launching the worker in an AppContainer without network capabilities, also without admin rights), but CHONK has no implementation or measurement of either. (Confidence: moderate for both mechanisms; CHONK-011 must measure, per [VALIDATION.md](../product/VALIDATION.md#4-privacy-and-execution-verification) §4.1.)

## 2. Decision rule

1. **Pilot OS** = the desktop OS on which the most recorded sessions perform the target job, weighted by reported weekly frequency of the job (field `B2` × `F1` in the [record template](../discovery/RECORD_TEMPLATE.md)).
2. **Tie or no clear majority:** Windows, because an official Ghostscript installer exists for it and install tolerance is a go/no-go criterion ([0004](0004-customer-hypothesis-and-go-no-go.md)).
3. **Override:** if CHONK-011 cannot demonstrate enforced worker egress denial on the selected OS, the pilot either runs there with privacy mode reported as unsupported (and participants told so in writing), or waits. It never runs with an unmeasured "local only" claim.
4. **Order after the pilot:** the other of Windows/macOS before broad v1 release; Linux remains a source-installed CLI until a participant or buyer needs it.
5. Record which OS versions and whether participants can install software without IT approval (field `F2`); a pilot on managed machines needs IT-approved installation, which changes packaging requirements (MSI/PKG, signing) in CHONK-024.

## 3. Outcome (filled after discovery)

| Field | Value |
|---|---|
| Sessions completed | 0 of 5 |
| Weighted OS tally | _pending_ |
| Selected pilot OS | _pending_ |
| Participants needing IT approval to install | _pending_ |
| Egress enforcement measured on selected OS (CHONK-011) | _not started_ |

## 4. Sign-off

| Item | Owner decision | Date | Reference |
|---|---|---|---|
| Decision rule §2 | _pending_ | | |
| Selected pilot OS §3 | _pending discovery_ | | |
