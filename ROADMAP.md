# Roadmap: from this repo to a resume line in 7 days

Every file this project needs is already here and working: 56 tests pass, the eval runs, and the UI is built. What's left is what no one can do for you: understanding it well enough to defend it in an interview, plugging in a real LLM to get **your** numbers, and shipping it to GitHub and AWS.

Each phase below has four parts:

- **Read**: the files to go through.
- **Run**: the commands to run.
- **Make it yours**: one real change to write yourself.
- **Be ready to explain**: questions you should be able to answer.

Commit at the end of each phase, so your history shows how the project came together.

> **The rule:** don't push anything you can't explain. Interviewers pick one file and ask "why did you do it this way?" If you used AI tools to help build it (you do in your workflow anyway), that's fine to say. Not understanding your own code is what hurts.

---

## Schedule

| When | Phase | Output |
|---|---|---|
| Thu Sep 24 – Fri Sep 25 | 0. Setup · 1. Extraction | Everything runs locally; first commits |
| Sat Sep 26 | 2. Validation + pipeline | Your own validation rule |
| Sun Sep 27 | 3. LLM + eval | **Your real numbers** in the README |
| Mon Sep 28 | 4. UI | Keyboard shortcuts + a 2-person usability test |
| Tue Sep 29 – Wed Sep 30 | 5. Ship | Public GitHub repo, green CI, live on AWS |
| Thu Oct 1 | 6. Resume | Bullets, demo GIF, LinkedIn post |
| Fri Oct 2 | Buffer | Before the BMO deadline (Oct 3) |
| Oct 3 – Oct 15 | Stretch A | One or two stretch goals before Remarcable (Oct 15) |
| Oct 15 – Oct 31 | Stretch B | Real documents + email intake before Entrust (Oct 31) |

Budget 2–4 hours a day. If a day slips, cut the "Make it yours" task for that phase, never Phase 3 or 5.

---

## Phase 0: Setup (1 hour)

**Run**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
python -m data.generate_invoices
pytest
python -m app.cli demo
cd web && npm install && npm run build && cd ..
flask --app wsgi run --port 5001    # open http://localhost:5001 (5000 is taken by AirPlay on macOS)
```

Click through it: open a "Needs review" invoice, fix a field, approve it, and check the dashboard.

**Then**
```bash
git init && git add . && git commit -m "Initial project structure"
```
Create a **private** GitHub repo for now and push. You'll make it public in Phase 5.

---

## Phase 1: Extraction (half a day)

**Read**: `app/extraction/pdf_text.py` → `parsing.py` → `rules.py`, then `data/generate_invoices.py` (skim the four `draw_*` functions).

**Run**
```bash
python -m evals.run_eval --extractor rules
```
Look at the "By layout" table: 100% on three layouts, about 26% on `freeform`. Open `data/synthetic/pdfs/inv_0004.pdf` and see why the regexes fail on it.

**Make it yours.** Canadian invoices usually print the supplier's GST/HST registration number (e.g. `GST/HST Reg. No. 123456789 RT0001`), and CRA requires it on invoices above a threshold amount (look up the current thresholds on canada.ca).
1. Print a registration number on 3 of the 4 layouts in the generator, and add it to the labels.
2. Add a `gst_number` field to `InvoiceData` (in `app/schemas.py`) and a regex to `rules.py` to extract it.
3. Add a test in `tests/test_rules_extractor.py`.

**Be ready to explain**
- Why does the eval have a layout the regexes were never written against?
- `04/08/2026`: April or August? What does the code do, and why is its confidence low? (`parsing.py`)
- Why search line by line instead of across the whole text? (the "Northern" bug: `test_patterns_do_not_cross_line_breaks`)

**Commit**: `Add GST/HST registration number extraction`

---

## Phase 2: Validation and the pipeline (1 day)

**Read**: `app/validation.py` → `app/processor.py` → `app/worker.py` → `app/models.py`, then `docs/architecture.md`.

**Run**
```bash
pytest tests/test_validation.py tests/test_pipeline.py -v
```

**Make it yours.** Add a `GST_NUMBER_MISSING` warning in `validate_invoice()`: if the invoice total is above the CRA threshold and no registration number was read. Plant it in the generator (the 4th layout without the number is already your test case), then check the eval counts it under "errors caught".

**Be ready to explain**
- Why is money stored in cents? What goes wrong with floats?
- What happens if the same PDF is uploaded twice? What if the same invoice arrives as a *different* PDF?
- The LLM API times out. Walk through what happens to the document (`process_document`, `MAX_ATTEMPTS`).
- Two workers run at once. Why don't they process the same document? What does `SKIP LOCKED` do? (`claim_next`, and `test_parallel_workers_never_process_a_document_twice`, which fails if you delete that line; try it)
- Why check a PO's *running* total, not just the single invoice against the PO amount?

**Commit**: `Add GST number compliance check`

---

## Phase 3: The LLM, and your real numbers (1 day). The most important phase

**Setup**: create an API key at console.anthropic.com and add a small amount of credit. With Haiku and text input, a full 80-document eval run should cost a few cents to well under a dollar; check the current pricing page. Put the key in `.env`.

**Read**: `app/extraction/llm.py` → `app/extraction/__init__.py` (the `HybridExtractor`).

**Run**
```bash
python -m evals.run_eval --extractor hybrid
python -m evals.run_eval --extractor llm
ANTHROPIC_MODEL=claude-sonnet-5 python -m evals.run_eval --extractor hybrid   # compare a bigger model
```

Put the numbers in the README "Results" table. **These are what go on your resume.** Look at every document under "Auto-approved but wrong" and understand why it happened. If the auto-approved precision is under 100%, try raising `REVIEW_CONFIDENCE_THRESHOLD` or lowering `LLM_ONLY` in `llm.py`, and write down the trade-off (touchless rate vs precision) in the README.

**Make it yours**: pick one:
- A prompt change that measurably improves a field, with before/after numbers.
- A cost row in the report: log `response.usage` tokens in `AnthropicClient` and compute cost per document.
- Gemini as a second provider (uncomment `google-genai`), compared in a table.

**Be ready to explain**
- Why not just use the LLM on everything? (cost, latency, and the numbers you just measured)
- How do you know a field is right without asking the model how sure it is? (`score_against_reference`)
- Why does a weak regex guess not count as a "disagreement"? (`test_weak_regex_guesses_do_not_veto_a_correct_llm_reading`)
- What stops the model from inventing a due date from "Net 30"? (the prompt, plus the eval checks `due_date` is null on that layout)
- What happens if the model returns malformed JSON?

**Commit**: `Record hybrid and LLM eval results` (+ your change)

---

## Phase 4: The review UI (half a day)

**Read**: `web/src/pages/DocumentPage.tsx`, `QueuePage.tsx`, `web/src/api.ts`, then `app/api.py`.

**Run**: `flask --app wsgi run --port 5001` in one terminal and `cd web && npm run dev` in another.

**Make it yours**
1. Keyboard shortcuts on the document page: `A` approve, `R` reject, `J`/`K` next/previous in the queue. Reviewers process hundreds a day, so this is a real throughput feature.
2. **Usability test with 2 friends**: give each one 5 invoices to review, time them, and note where they hesitate. Fix the biggest issue and write 3–4 lines about it in the README. This is gold for the BMO UX/UI application.

**Be ready to explain**
- Why show the PDF as rendered images instead of an embedded PDF? (mobile browsers)
- Why must an override have a note? (audit trail: who approved what, and why)
- What happens when a reviewer corrects the original of a duplicate? (`revalidate_related`)

**Commit**: `Add reviewer keyboard shortcuts`

---

## Phase 5: Ship it (1–2 days)

1. **GitHub**: make the repo public. Check that `.env` is **not** committed (`git log --all -- .env` should print nothing). Add a short description and topics (`python`, `flask`, `react`, `llm`, `automation`, `postgresql`).
2. **CI**: push, and watch the Actions tab go green (lint, tests on SQLite and Postgres, eval gate, UI build, Docker build). Add the badge to the top of the README:
   `![CI](https://github.com/YOUR_USERNAME/invoiceflow/actions/workflows/ci.yml/badge.svg)`
   To gate the LLM in CI too, add `ANTHROPIC_API_KEY` as a repository secret and add a second eval step with `--extractor hybrid`.
3. **Deploy**: follow `docs/deploy-aws.md`. Set `BASIC_AUTH_USER`/`PASSWORD`. Use S3 storage with an IAM role, not access keys.
4. **Demo GIF**: record 20 seconds of uploading → review → fix a field → approve (Kap on Mac, ScreenToGif on Windows), save it as `docs/demo.gif`, and put it at the top of the README.

**Be ready to explain**: how the app gets S3 access without keys; what happens to data if the server dies (backups: `deploy/backup.sh`); why the Docker image is built in two stages.

---

## Phase 6: Resume and applications (half a day)

Replace every `[ ]` with **your measured numbers**, and don't round up. Pick 2–3 bullets per application.

**Core**
- Built InvoiceFlow, an invoice-processing pipeline (Python, Flask, PostgreSQL, React/TypeScript, Claude API) that extracts, validates and routes invoices, auto-approving [ ]% with no human touch at [ ]% precision on an 80-invoice evaluation set
- Designed a hybrid extractor (regex first, LLM fallback) that raised field accuracy on unseen invoice layouts from 25.7% to [ ]% while making [ ]% fewer LLM calls than an LLM-only approach
- Wrote an evaluation harness that gates CI on extraction accuracy and auto-approval precision, catching [ ]/22 planted errors (duplicates, PO overruns, total mismatches) with [ ] false alarms

**Backend / reliability** (IBM, RBC, Intelcom, Ross Video)
- Made processing safe to retry and parallelize: SHA-256 idempotent ingest, Postgres `FOR UPDATE SKIP LOCKED` job claiming verified by a concurrency test, bounded retries with failure tracking; deployed with Docker Compose on AWS EC2 with S3 storage

**Data / automation** (Nutrien, Delta)
- Built PostgreSQL reporting views for vendor spend, PO utilization and review-queue aging, plus a CSV export for accounting systems

**QA** (SECURE Energy)
- Covered the pipeline with 56 pytest tests running against SQLite and PostgreSQL in GitHub Actions, including mutation-checked concurrency tests and a regression gate on extraction accuracy

**UX** (BMO)
- Designed a reviewer interface (React/TypeScript) showing the source document next to confidence-scored fields; ran a usability test with [ ] users and cut average review time from [ ]s to [ ]s

---

## Stretch goals (after Oct 3)

In rough order of resume value:

1. **Real documents.** Add 20–30 real invoices and receipts (your own receipts, or a public dataset such as SROIE, the ICDAR 2019 scanned-receipts dataset), label them in the UI by reviewing and approving them, then run `python -m evals.export_reviewed` → eval on `data/reviewed`. Real-world accuracy beats synthetic every time.
2. **Email intake.** A dedicated inbox (IMAP with Python's `imaplib`, or AWS SES → S3) where PDF attachments get ingested automatically. This makes the "automation" story end to end.
3. **Alembic migrations** instead of `create_all`, so schema changes don't require wiping the database.
4. **Real user accounts** (Flask-Login or an OAuth provider) replacing the shared password, with the reviewer name taken from the login.
5. **Alerts**: post to a Slack or Discord webhook when a document fails or the review queue gets old.
6. **Scanned invoices**: print a sample invoice, photograph it, and confirm the LLM path handles it (PDFs with no text are already sent to the model as a document). Add these to the eval.
7. **Load test**: 1,000 documents, 1 vs 4 workers, and throughput in the README.

---

## If you get stuck

- `pytest -x -v` stops at the first failure and shows it.
- Everything the pipeline decided is in the database: `sqlite3 instance/invoiceflow.db "select id, status, last_error from documents"`.
- Start over with the demo data: delete `instance/`, then `python -m app.cli demo`.
