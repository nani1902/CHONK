# Portal upload requirements (Phase 0 sample)

Collected 2026-09-24 to choose benchmark targets. **Every row is from secondary
sources** (exam-prep sites, immigration blogs, search-engine summaries). The
official pages (ircc.canada.ca, immigration.govt.nz, immi.homeaffairs.gov.au,
scholarships.gov.in) were blocked from the environment this was researched in.
Treat every number as "reported", confirm it on the portal before building a
preset on it, and re-check before each exam season: these change.

Confidence: **M** = several independent secondary sources agree; **L** = one
source, or sources disagree.

## PDF documents

| Portal | Document | Format | Size | Conf. | Sources |
|---|---|---|---|---|---|
| UPSC (India) online application | Photo ID proof | PDF | 20–300 KB | M | [testbook](https://testbook.com/upsc-civil-services/how-to-fill-upsc-online-form), [examformtools](https://examformtools.in/blog/upsc-photo-signature-size-latest-document-requirements/) |
| UPSC | Class 10 certificate, degree certificate and marksheets | PDF | 50–300 KB | L | [thebetterindia](https://thebetterindia.com/web-stories/civil-servants/upsc-cse-2026-application-document-checklist-photo-signature-guidelines-india-11155690) |
| NTA NEET-UG (India) | Category certificate | PDF | 50–300 KB | M | [pw.live](https://www.pw.live/news/neet-ug-2026-registration-documents-required-size-format-nta), [collegedunia](https://collegedunia.com/exams/neet/photo-size-and-signature-guidelines) |
| National Scholarship Portal (India) | Each document | PDF or JPEG | ≤ 200 KB (older schemes: ≤ 500 KB) | M | NSP FAQ PDF (via search), [fatafatpdf](https://fatafatpdf.com/blog/compress-pdf-for-nsp-scholarship/), [utilitylab](https://www.utilitylab.in/blog/scholarship-portal-document-upload-guide/) |
| GATE GOAPS (India) | Category certificate | PDF | about 100–500 KB | L | [collegedunia](https://collegedunia.com/exams/gate/photo-size-and-signature-guidelines) |
| IRCC (Canada), most online accounts | Each file | PDF, JPG and others | ≤ 4 MB (some accounts 5 MB) | M | IRCC help centre q1123 (via search), [moving2canada](https://moving2canada.com/immigration/documents/format-file-size-documents-canada-immigration-ircc/) |
| IRCC Webform | Each file / total | PDF and others | ≤ 2 MB each, 3.5 MB total | L | [lawyerinfo.ca](https://lawyerinfo.ca/guides/immigration-visas/citizenship/maximum-file-size-and-pdf-compression-tips-for-ircc-webform-uploads/) |
| UK visas: UKVCAS / VFS / TLScontact | Each file | PDF, JPG, PNG | 6 MB / 2 MB / 4 MB | L | [findmyvisa](https://findmyvisa.co.uk/how-to/upload-to-ukvi-portal) |
| Australia ImmiAccount | Each attachment | PDF and 14 other types | ≤ 5 MB; 30–60 files per application | M | Home Affairs help pages (via search), [migratio](https://migratio.com.au/blog/immiaccount-file-upload-requirements) |
| USCIS online filing (US) | Each evidence file | PDF, JPG (TIFF on some forms) | ≤ 12 MB (some sources say 6 MB) | L | USCIS "Tips for filing forms online" (via search) |
| CIBTvisas (visa service) | Each file | PDF | ≤ 10 MB | L | [cibtvisas](https://cibtvisas.com/upload-forms) |

## Images (JPEG). CHONK cannot produce these today

| Portal | Item | Size | Pixels | Conf. | Sources |
|---|---|---|---|---|---|
| Passport Seva (India) | Photo | < 250 KB | exactly 630 × 810 | M | [photopass](https://www.photopass.ai/blog/how-to-upload-photo-passport-seva), Passport Seva upload instructions PDF |
| Passport Seva | Signature | < 100 KB | rectangular crop | M | [photopass](https://www.photopass.ai/blog/passport-seva-signature-upload-guide) |
| UPSC | Photo / signature | 20–300 KB / 20–100 KB | 350–1000 px / 350–500 px | M | [vajiramandravi](https://vajiramandravi.com/upsc-exam/upsc-photo-and-signature-guidelines/), [freedigitaltools](https://freedigitaltools.in/blog/upsc-photo-signature-guide-2026) |
| IBPS (bank exams) | Photo / signature / left thumb / handwritten declaration | 20–50 / 10–20 / 20–50 / 50–100 KB | 200×230 / 140×60 / 240×240 / 800×400 | M | [adda247](https://www.adda247.com/jobs/ibps-rrb-photo-and-signature-size/), [truejobs](https://truejobs.co.in/blog/ibps-photo-signature-thumb-declaration-2026-file-size-format-guide) |
| SSC one-time registration | Photo / signature | 20–50 KB / 10–20 KB | signature about 6 × 2 cm | M | [pw.live](https://www.pw.live/ssc/exams/ssc-cgl-photo-and-signature-size-2026), [testbook](https://testbook.com/news/ssc-gd-signature-photo-size/) |
| NTA NEET-UG | Photo / signature | 10–200 KB / 10–100 KB | | M | [pw.live](https://www.pw.live/neet/exams/photo-and-signature-upload-guidelines-for-neet-2026) |
| GATE GOAPS | Photo / signature | 5–600 KB / 3–300 KB | 200×260–530×690 / 250×80–580×180 | L | [collegedunia](https://collegedunia.com/exams/gate/photo-size-and-signature-guidelines) |

## What this implies before any benchmark

1. **Two regimes.** Indian exam and scholarship portals want PDFs of
   **100–300 KB**. Visa portals allow **2–12 MB**. A two-page 300 dpi colour
   scan is usually 2–6 MB, so the visa regime is mostly easy, and the small-PDF
   regime is where compression quality decides legibility.
2. **Minimum sizes exist.** Many portals reject files *below* a floor (for
   example, a signature of at least 10 KB). A tool that only enforces a maximum
   can produce a file the portal rejects. CHONK has no minimum today.
3. **Half the demand is images, not PDFs.** Photo, signature, thumb impression
   and declaration uploads are JPEGs with exact pixel sizes. A PDF-only product
   misses them.
4. **Some failures are silent.** Several secondary sources say oversized files
   on VFS and UKVCAS fail without an error, and recommend staying well under the
   stated limit. This is unverified, but if true it argues for a safety margin
   in presets.
