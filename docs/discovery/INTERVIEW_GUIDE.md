# Discovery interview and observation guide

For CHONK-002. Produces one [content-free record](RECORD_TEMPLATE.md) per session, feeding decisions [0002](../decisions/0002-size-limit-units.md), [0003](../decisions/0003-pilot-platform.md), and [0004](../decisions/0004-customer-hypothesis-and-go-no-go.md). Session length: 45–60 minutes. CHONK is **not** shown or installed during these sessions; they study the current workflow.

## Rules that are not optional

1. **No documents leave the participant.** Do not accept, request, photograph, screenshot, or record any real document, file name, client name, or portal account. If a participant starts sharing a screen with client content, ask them to stop and switch to the practice file.
2. **Observation uses a non-sensitive file.** Ask the participant to use a PDF they are comfortable showing (a public form, a blank template, a page they print and scan themselves) that resembles their usual file class. Record the class (scan / text / mixed) and approximate size, never the content.
3. **Record behavior over opinion.** "Last time you did this, what happened?" outranks "Would you use…?". Mark every value as *observed*, *described* (a recalled specific instance), or *estimated* (a general guess).
4. **Consent.** Read the consent script. No audio/video recording unless the participant agrees; if recorded, the recording stays with the interviewer, is deleted after the record is written, and never enters the repository.
5. **Participant IDs only.** Use `P1`–`P5`. Keep any mapping from IDs to people outside the repository.
6. **Do not pitch.** Do not describe CHONK's features until section G. Describing the solution first contaminates the answers about the problem.

## Consent script

> We're studying how people prepare PDF files for upload limits, to decide whether a local tool is worth building. We will not look at or keep any of your real documents. I'll take notes on steps, times, and problems — not on what your documents contain. You can skip any question or stop at any time. May I take notes? May I time the steps you show me?

## A. Screener (before scheduling)

- A1. In the last month, have you had to make a PDF smaller to upload or email it? (No → screen out; record reason.)
- A2. Is this for your own documents or for other people (clients, students, applicants)?
- A3. Roughly how many people work in your organization? Is it for-profit, educational, governmental, or charitable? (Needed for [0001](../decisions/0001-licensing-and-distribution.md) eligibility, not for selling.)
- A4. Which computer do you do this on: Windows, Mac, Linux, or a phone/tablet?

## B. Context (5 min)

- B1. How often do you prepare files for size-limited uploads? When was the last time? The time before?
- B2. On which device and OS? Is it a work-managed machine?
- B3. Which kinds of files: scanned papers, statements downloaded as PDF, forms, photos converted to PDF? Roughly how many pages and MB?

## C. Walk-through of the last real instance (15 min)

Ask them to recall the most recent instance step by step, then (section D) reproduce it on the practice file.

- C1. What was the limit, exactly as the portal showed it? (Literal text, e.g. "Max 2MB", "< 5 MB each", "10 MB total".)
- C2. What did you do first? Then? Which tool(s)?
- C3. How many attempts until it was accepted? Why did attempts fail (too big, rejected, unreadable, wrong format)?
- C4. How long did it take from "this file is too big" to "uploaded"?
- C5. Has a portal ever rejected a file that your computer said was under the limit? (Probe the Windows/macOS unit difference in [0002](../decisions/0002-size-limit-units.md) §3 without explaining it.)
- C6. Has anyone ever complained the uploaded file was unreadable or missing pages? What happened?

## D. Observation on the practice file (10 min)

Ask them to make the practice file fit a limit of your choosing that forces compression (for example half its current size, stated in the unit their portals use). Time with a stopwatch. Record: tool, each step, retries, time to a result they would accept, whether they checked the result and how (opened it? zoomed?), and any unit confusion. Do not help.

## E. Sensitivity constraints (5 min)

- E1. Are there documents you are not allowed to, or would not, put into a website to compress? Who decided that — a written policy, a client agreement, your own judgment?
- E2. What do you do with those? (Behavior, not attitude.)
- E3. Have you used an online PDF tool in the last month for work files? (Ask neutrally; do not judge.)

## F. Install tolerance and platform (5 min)

- F1. How many times per week does this happen on each device you use? (Weights [0003](../decisions/0003-pilot-platform.md).)
- F2. Can you install software on this machine yourself? If not, who approves and how long does it take?
- F3. Have you installed a desktop utility for work in the last year? What made you willing?
- F4. If a tool required installing a second free program (explain: a PDF engine called Ghostscript) would that stop you?

## G. Reaction and pilot (5–10 min, last)

Only now describe CHONK in one sentence: *a program on your own computer that makes a PDF fit an exact size limit, checks that pages and text survived, and tells you when it can't do it safely.*

- G1. When would that have helped in the instance you described? When would it not?
- G2. What would you need to see to trust the output without opening it?
- G3. Would you try it for two weeks on your own machine? For commercial participants, explain that this requires signing a short no-fee evaluation license ([0001](../decisions/0001-licensing-and-distribution.md) §5); record willingness, do not sign anything in the session.
- G4. (Developers only) How does your agent currently handle PDFs that are too large? What would it need back from a tool to continue without a human?

## After the session

Write the [record](RECORD_TEMPLATE.md) within 24 hours, delete raw notes that contain anything identifying, and update the [synthesis](SYNTHESIS.md). Commit only the content-free record.
