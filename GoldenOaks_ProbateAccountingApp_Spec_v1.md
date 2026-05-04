# GOLDEN OAKS | Probate Accounting Excel Generator — Developer Specification
**v1.0 | May 2026**

*Golden Oaks Law Firm | Confidential | May 2026*

---

## 1. Project Overview

Golden Oaks staff currently spend significant time manually transcribing financial transactions from bank statements, brokerage statements, credit card statements, and retirement account statements into Excel-based accounting schedules required for California court filings. This work is tedious, error-prone, and a poor use of attorney/paralegal time.

This app eliminates the manual transcription work by:

1. Accepting multiple financial statements at once (a full accounting period)
2. Using Gemini AI to extract every transaction from every statement
3. Categorizing each transaction into the correct California probate accounting schedule
4. Presenting a side-by-side review interface so staff can verify each transaction against the source statement
5. Generating a final Excel file using the firm's existing template, with all transactions placed in the correct schedules

The app supports three California fiduciary accounting types — **Conservatorships**, **Probate Estates**, and **Trust Administration** — each with their own statutory schedule requirements.

This app is a **fully standalone application**, deployed independently from Apps 1 and 2, but built using the **same architectural patterns and tech stack** to keep the firm's tech stack consistent.

---

## 2. Scope

### In Scope — V1
- Multi-document upload (full accounting period at once)
- Support for: bank statements (checking, savings), brokerage/investment statements, credit card statements, retirement account statements
- Matter type selection: Conservatorship / Probate Estate / Trust Administration
- AI-powered transaction extraction via Google Gemini
- AI-powered transaction categorization into California probate schedules
- Cross-statement reconciliation (duplicate detection, transfer matching)
- Side-by-side human verification UI (source statement viewer + editable transaction grid)
- Excel output using the firm's existing template
- Audit trail mapping every transaction line back to its source statement and page
- Microsoft Entra SSO authentication

### Out of Scope — V1
- Direct e-filing to California courts
- Generation of cover documents (petition, declaration, notice)
- Cost-basis lookups for securities not listed on the source statement
- Tax document generation (1099s, K-1s)
- Multi-period accounting (each session covers one accounting period only)
- Auto-import from financial institutions via APIs (Plaid, Yodlee, etc.)
- Generation of supporting inventory/appraisal schedules from non-financial sources

---

## 3. Recommended Tech Stack

> Standalone application — uses the same patterns as Apps 1 & 2 but is fully independent (separate repo, deployment, database, and credentials). See `FOUNDATION.md` for the shared stack reference.

| Layer | Technology | Notes |
|---|---|---|
| Language | Python 3.12 | Same as App 1 / App 2 foundation |
| Web Framework | Streamlit | Entrypoint `app/streamlit_app.py`, port 8501 |
| AI / Extraction | Google Gemini API | `gemini-1.5-pro` — long context handles multi-page brokerage statements |
| PDF / Text | pdfplumber, PyMuPDF, pdf2image, pytesseract | Same extraction pipeline as App 1 |
| Schema Validation | Pydantic v2 | Same as App 1 |
| Auth | MSAL (Microsoft Entra) | Same pattern as App 1 |
| Excel Output | openpyxl | Required for preserving the firm's existing template formatting/formulas |
| PDF Display | Streamlit + base64-embedded PDFs, or `streamlit-pdf-viewer` | For side-by-side review |
| Local State | SQLite (via SQLAlchemy) | Tracks accounting sessions, transactions, and categorization decisions |
| Hosting | Coolify (self-hosted Docker) | Same deployment target as Apps 1 & 2 |

---

## 4. Application Flow

### Step 1 — New Accounting Session
- Staff log in via Entra SSO
- Click "Start New Accounting"
- Enter session metadata:
  - Matter name / matter ID
  - Matter type: **Conservatorship / Probate Estate / Trust Administration**
  - Accounting period start date
  - Accounting period end date
  - Beginning balance(s) for each account (optional — can be auto-detected)

### Step 2 — Multi-Document Upload
- Drag & drop zone accepts multiple PDFs at once
- Each uploaded file is tagged with detected institution and account type
- Staff can manually re-tag if AI mis-detects
- App displays a summary: e.g., "12 statements uploaded — 4 institutions, 7 accounts, period 1/1/2025 – 12/31/2025"

### Step 3 — Text Extraction
- Each PDF goes through the text extraction pipeline (pdfplumber → OCR fallback)
- For brokerage statements, table extraction via pdfplumber's table-finding logic
- Status updates: `pending_extraction` → `extracted`

### Step 4 — AI Transaction Extraction
- For each statement, Gemini extracts every transaction with:
  - Date
  - Description (as printed on statement)
  - Amount
  - Type (debit / credit / dividend / interest / fee / transfer / trade / etc.)
  - Running balance (if present)
  - Page number (for audit trail)
- Returns structured JSON validated by Pydantic
- Status updates: `extracted` → `pending_categorization`

### Step 5 — AI Schedule Categorization
- All transactions across all statements are sent to Gemini with:
  - The matter type (Conservatorship / Probate Estate / Trust Administration)
  - The list of available schedules and their descriptions
  - Instruction to assign each transaction to the most appropriate schedule
  - Instruction to flag any transaction it cannot confidently categorize
- Gemini returns a categorization for every transaction with a confidence score
- Status updates: `pending_categorization` → `pending_review`

### Step 6 — Cross-Statement Reconciliation
- App runs deterministic checks:
  - Detect duplicate transactions (same date + amount on two related accounts)
  - Match transfers between matter accounts (Schedule excluded — internal moves)
  - Verify ending balance of statement N = beginning balance of statement N+1 for each account
  - Flag any reconciliation issues for staff review

### Step 7 — Side-by-Side Verification
- Staff review every transaction with the source statement visible
- See Section 7 for full UI spec

### Step 8 — Excel Generation
- App opens the firm's master Excel template (loaded from configured location)
- Populates each schedule sheet with the verified transactions
- Preserves all template formulas, formatting, and totals
- Adds a hidden audit trail sheet mapping each transaction to source file + page
- Generates a downloadable `.xlsx` file
- Status updates: `pending_review` → `completed`

---

## 5. California Probate Accounting Schedules

The app must categorize every transaction into one of the following schedules. These align with California Probate Code §§ 1060–1064 (general) and §§ 2620–2623 (conservatorships).

| Schedule | Name | Includes | Excludes |
|---|---|---|---|
| **A** | Property on Hand at Beginning / Inventory | Opening account balances, securities held, real property | Income earned during period |
| **B** | Receipts | Interest, dividends, rental income, refunds, sale proceeds (principal portion) | Internal transfers, gains on sale |
| **C** | Gains on Sales | Realized gains on sold securities or property | Unrealized gains, dividend reinvestments |
| **D** | Disbursements | Bills paid, taxes, insurance, repairs, professional fees, bank fees | Distributions to beneficiaries, fiduciary compensation |
| **E** | Losses on Sales | Realized losses on sold securities or property | Unrealized losses |
| **F** | Distributions | Payments to beneficiaries, conservatee personal needs allowance | Fiduciary compensation, expenses |
| **G** | Property on Hand at End | Ending account balances, securities held, real property | Income/expenses |
| **H** | Liabilities | Mortgages, loans, unpaid bills as of period end | Paid disbursements |
| **I** | Fiduciary Compensation | Trustee fees, conservator fees, attorney fees | Other professional fees → D |

### Matter-Type Specific Variations

**Conservatorships (Probate Code § 2620):**
- Schedule for personal needs allowance is broken out
- Bond information often required
- Court order references on disbursements

**Probate Estates (Probate Code § 1061):**
- Inventory & Appraisal cross-references required
- Creditor claim payments tracked separately within Schedule D

**Trust Administration (Probate Code § 16063, § 1064):**
- Principal vs. Income distinction required (some schedules split)
- Trustee compensation may have separate principal/income allocation

The exact column structures will be driven by the firm's master Excel template.

---

## 6. Gemini API Integration

### Two-Stage AI Pipeline
This app uses **two distinct Gemini calls** per statement, by design:

1. **Extraction call** — pulls every transaction from a statement into structured form
2. **Categorization call** — assigns each transaction to a schedule

This separation matters because extraction is mechanical and benefits from a focused prompt, while categorization is judgment-heavy and benefits from full context across all statements.

### Stage 1 — Transaction Extraction Prompt
Instruct Gemini to:
- Act as a financial document analyst
- Identify the financial institution, account type, account number (last 4 digits only), and statement period
- Extract every transaction with date, description, amount (signed), type, page number
- For brokerage statements: also capture security symbol, quantity, price, cost basis if present
- Return ONLY a valid JSON object — no preamble, no markdown
- Return `null` for any field not found — never infer

### Stage 1 Response Structure
```json
{
  "institution": "Wells Fargo Bank",
  "accountType": "Checking",
  "accountNumberLast4": "1234",
  "statementPeriodStart": "2025-01-01",
  "statementPeriodEnd": "2025-01-31",
  "beginningBalance": 12450.32,
  "endingBalance": 13120.78,
  "transactions": [
    {
      "date": "2025-01-03",
      "description": "INTEREST PAYMENT",
      "amount": 2.14,
      "type": "interest",
      "balance": 12452.46,
      "sourcePage": 1
    },
    {
      "date": "2025-01-15",
      "description": "ELECTRIC BILL - SCE",
      "amount": -184.22,
      "type": "debit",
      "balance": 12268.24,
      "sourcePage": 1
    }
  ],
  "flags": ["Page 3 contained partial OCR — verify totals"]
}
```

### Stage 2 — Categorization Prompt
Instruct Gemini to:
- Act as a California probate accounting specialist
- Receive the matter type and the firm's schedule definitions
- Assign every transaction to exactly one schedule
- Return a confidence score per transaction: `high` / `medium` / `low`
- Return brief reasoning for medium/low confidence categorizations
- Flag transfers between matter-owned accounts as `internal_transfer` (excluded from schedules)
- Flag transactions it cannot categorize with `needs_review`

### Stage 2 Response Structure
```json
{
  "categorizations": [
    {
      "transactionId": "txn_001",
      "schedule": "B",
      "subcategory": "Interest Income",
      "confidence": "high",
      "reasoning": "Standard bank interest payment"
    },
    {
      "transactionId": "txn_002",
      "schedule": "D",
      "subcategory": "Utilities",
      "confidence": "high",
      "reasoning": "Standard utility disbursement"
    },
    {
      "transactionId": "txn_003",
      "schedule": "needs_review",
      "subcategory": null,
      "confidence": "low",
      "reasoning": "Description 'WIRE OUT - REFERENCE 4421' is ambiguous — could be distribution or transfer"
    }
  ]
}
```

### Model Settings
- Model: `gemini-1.5-pro`
- Temperature: `0` (deterministic financial data)
- Max output tokens: `8192` for extraction (long statements), `4096` for categorization

### API Key & Data Privacy
- Same handling as Apps 1 & 2 — Gemini API key stored as Coolify environment variable
- Document content must never be logged or persistently stored
- **Strongly recommend Vertex AI endpoint** for this app due to the highly sensitive nature of financial data
- Consider data residency — confirm with firm IT that Gemini processing region is acceptable

---

## 7. Side-by-Side Verification UI

This is the most important UI in the app and where staff will spend most of their time.

### Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│ [Header: Matter Name | Period | Statement: Wells Fargo - 1234]      │
├──────────────────────────┬──────────────────────────────────────────┤
│                          │  TRANSACTIONS — Wells Fargo Checking ...4│
│                          │  ┌────────────────────────────────────┐  │
│   [PDF VIEWER]           │  │ ☐ 1/3   INTEREST       +$2.14      │  │
│   Source statement       │  │   Schedule: B  Confidence: ●●● ✏️   │  │
│   currently being        │  │ ☐ 1/15  ELECTRIC BILL  -$184.22    │  │
│   reviewed               │  │   Schedule: D  Confidence: ●●● ✏️   │  │
│                          │  │ ⚠ 1/22  WIRE OUT       -$5,000.00  │  │
│   Page 1 of 6            │  │   Schedule: ?  Confidence: ●○○      │  │
│   [Prev] [Next]          │  │   ▼ Needs review                    │  │
│                          │  │ ...                                 │  │
│                          │  └────────────────────────────────────┘  │
│                          │  [Approve All High-Confidence]           │
│                          │  [Next Statement →]                      │
└──────────────────────────┴──────────────────────────────────────────┘
```

### Left Pane — PDF Viewer
- Shows the currently selected source statement
- When staff click on a transaction in the right pane, the PDF auto-jumps to the source page
- Page navigation (prev/next/jump-to-page)
- Zoom controls

### Right Pane — Transaction Grid
- Lists transactions for the currently selected statement
- Each row shows: date, description, amount, AI-suggested schedule, confidence indicator
- Color-coded confidence badges: green (high), amber (medium), red (low / needs review)
- Inline editable schedule dropdown
- Inline editable subcategory field
- Notes field per transaction
- Checkbox to mark "verified" (or use bulk-approve action)
- Filter: all / high confidence / needs review / unverified

### Statement Navigation
- Sidebar listing all uploaded statements
- Each shows: institution, account, period, verification progress (e.g., "23/45 verified")
- Color indicator: gray (not started) / amber (in progress) / green (complete)

### Reconciliation Banner
- Top of the page, shows:
  - Number of detected duplicate transactions
  - Number of detected internal transfers
  - Any beginning/ending balance mismatches
- Click expands to show details and lets staff confirm or override

### Bulk Actions
- "Approve all high-confidence transactions in this statement"
- "Approve all high-confidence transactions across all statements"
- "Mark all internal transfers as confirmed"

### Export Button
- Greyed out until all transactions across all statements are either verified or explicitly marked as excluded
- Click triggers Excel generation (Step 8)

---

## 8. Excel Output

### Template-Driven
The app does not generate Excel from scratch. It opens the firm's existing master template and populates predefined cells/ranges. This preserves all formulas, totals, formatting, and headers the firm has already built.

### Template Configuration
The firm's master Excel template is configured via a JSON mapping file that tells the app:
- Which sheet corresponds to each schedule (A, B, C, D, E, F, G, H, I)
- Which cell ranges contain the data rows for each schedule
- Which columns contain: date / description / amount / payee / category / notes
- Which named cells (if any) hold totals or matter metadata

A separate template + mapping file exists per matter type (Conservatorship / Probate / Trust).

### Generation Flow
1. Load the appropriate template based on matter type
2. For each schedule, write transaction rows starting from the configured first data row
3. Insert rows as needed to accommodate all transactions
4. Write matter metadata (name, period, totals) to named cells
5. Append a hidden `_AuditTrail` sheet mapping every populated row to: source file name, source page number, AI confidence, staff verifier email, verification timestamp
6. Save as a new file with auto-generated name (see below)

### File Naming
```
[MatterName]_Accounting_[PeriodStart]_to_[PeriodEnd]_[GeneratedDate].xlsx
```
Example: `Smith_Conservatorship_Accounting_2025-01-01_to_2025-12-31_2026-05-03.xlsx`

### Audit Trail Sheet
Hidden by default. Columns:
- Schedule
- Row in schedule
- Source File
- Source Page
- Original Description
- Amount
- AI Schedule Suggestion
- AI Confidence
- Final Schedule (after verification)
- Edited By Staff?
- Verified By
- Verification Timestamp

This sheet is critical for partner review and any future court inquiry.

---

## 9. Cross-Statement Reconciliation

### Duplicate Detection
- Same amount, same date (±1 day), opposite signs, on two accounts owned by the matter → flagged as potential internal transfer
- Same amount, same date, same description on the same account → flagged as potential duplicate

### Transfer Matching
- When two transactions match (one credit, one debit) across two matter accounts, they are auto-grouped as `internal_transfer`
- These transactions are **excluded** from the final Excel schedules but retained in the audit trail
- Staff can override and treat them as separate transactions if needed

### Balance Verification
- Beginning balance on statement N+1 should equal ending balance on statement N for each account
- Mismatches are flagged with the discrepancy amount
- Common causes (timing differences across statement-cycle dates) are explained in the flag

### Period Coverage
- App verifies the uploaded statements cover the full accounting period without gaps
- Flags any account that has missing months in the period

---

## 10. Module Architecture

> Same naming conventions as Apps 1 & 2. Independent codebase.

| Module | Responsibility |
|---|---|
| `app/config.py` | Settings from environment |
| `app/streamlit_app.py` | UI + OAuth callback handling |
| `app/auth_entra.py` | MSAL confidential client |
| `app/pdf_ingest.py` | PDF validation |
| `app/text_extract.py` | PDF text extraction with OCR fallback |
| `app/gemini_extractor.py` | Stage 1 — transaction extraction Gemini wrapper |
| `app/gemini_categorizer.py` | Stage 2 — schedule categorization Gemini wrapper |
| `app/models.py` | Pydantic models — `Transaction`, `Statement`, `Categorization`, `AccountingSession` |
| `app/reconciler.py` | Cross-statement duplicate / transfer / balance reconciliation |
| `app/schedules.py` | California probate schedule definitions, matter-type variants |
| `app/excel_writer.py` | openpyxl-based template population with audit trail |
| `app/template_config.py` | Loads/validates JSON template mapping files |
| `app/db.py` | SQLite session + transaction storage during review |
| `app/admin_settings_store.py` | Admin-editable Gemini prompts and template paths |

---

## 11. Environment Variables

| Variable | Role |
|---|---|
| `GEMINI_API_KEY` | Required for AI |
| `GEMINI_MODEL` | Default `gemini-1.5-pro` |
| `APP_ENV` | `development` / `production` |
| `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET`, `ENTRA_REDIRECT_URI` | MSAL — separate Entra app registration from Apps 1 & 2 |
| `ENTRA_SCOPES` | Default `User.Read` |
| `ENTRA_ADMIN_ROLE` | App role for admin features (default `Accounting.Admin`) |
| `TEMPLATE_PATH_CONSERVATORSHIP` | Path to master Excel template for conservatorships |
| `TEMPLATE_PATH_PROBATE` | Path to master Excel template for probate estates |
| `TEMPLATE_PATH_TRUST` | Path to master Excel template for trust administration |
| `TEMPLATE_MAPPING_PATH` | Path to JSON file defining sheet/cell mappings for each template |
| `MAX_FILE_SIZE_MB` | Per-file max size (default 50) |
| `MAX_SESSION_FILES` | Max statements per session (default 50) |
| `SQLITE_DB_PATH` | Local DB path (default `data/accounting.db`) |
| `SKIP_ENTRA_AUTH` | Local dev only |

---

## 12. Security & Compliance Requirements

> ⚠ **Critical — Financial data is highly sensitive. Confirm with Golden Oaks IT and compliance officer before launch.**

- **Vertex AI strongly preferred over standard Gemini API** for stronger enterprise data protections
- Gemini API key stored as Coolify environment variable — never hardcoded
- Statement content never logged or persisted beyond active session
- Account numbers truncated to last 4 digits in all UI and stored data
- Full statements deleted from disk immediately after processing completes
- SQLite database encrypted at rest if firm policy requires (use SQLCipher)
- Coolify instance hosted on firm-approved infrastructure with HTTPS enforced
- Session timeout enforced — staff re-authenticate after extended idle periods
- Generated Excel files only delivered via direct download — never emailed or stored on third-party services
- All staff actions (verifications, edits, schedule changes) logged with email + timestamp
- Audit trail sheet in every Excel output — non-removable record of source and verifier

---

## 13. Assets & Information Needed From Golden Oaks

| Item | Format | Status | Notes |
|---|---|---|---|
| Master Excel template — Conservatorship | .xlsx | ⏳ Pending | Existing firm template |
| Master Excel template — Probate Estate | .xlsx | ⏳ Pending | Existing firm template |
| Master Excel template — Trust Administration | .xlsx | ⏳ Pending | Existing firm template |
| Template mapping documentation | Per template | ⏳ Pending | Which sheets/cells/columns map to which schedule data |
| Sample completed accounting | .xlsx + source PDFs | ⏳ Pending | For testing categorization accuracy end-to-end |
| Categorization rules / firm conventions | Document or notes | ⏳ Optional | Any firm-specific schedule treatment beyond standard probate code |
| New Entra app registration | Tenant + client config | ⏳ Pending | Separate from Apps 1 & 2 |
| Gemini / Vertex AI key | Credentials | ⏳ Pending | Vertex AI strongly preferred for this app |
| Coolify infrastructure access | Same instance, separate deployment | ⏳ Pending | App 3 deploys as its own Coolify resource |
| New domain or subdomain | e.g., `accounting.goldenoaks.com` | ⏳ Pending | Distinct from Apps 1 & 2 |
| Sample bank, brokerage, credit card, retirement statements | PDFs | ⏳ Optional | Recommended for testing extraction accuracy |

---

## 14. Coolify Deployment

Deployed as a **separate Coolify resource** from Apps 1 & 2.

### Required Files
- `Dockerfile` — Python 3.12 base, Tesseract install, Streamlit launch
- `requirements.txt` — Python dependencies including `openpyxl`, `apscheduler` (optional), `sqlalchemy`
- `docker-compose.yml` — defines persistent volume for SQLite database and template files

### Persistent Volumes
Two volumes are required:
```yaml
volumes:
  - ./data:/app/data        # SQLite database
  - ./templates:/app/templates  # Master Excel templates
```

### Deployment Steps
1. Create a new Coolify application
2. Connect to this app's own Git repository
3. Upload master Excel templates to the templates volume
4. Set environment variables
5. Mount persistent volumes for `/app/data` and `/app/templates`
6. Enable HTTPS via Coolify Let's Encrypt
7. Set up distinct custom domain (e.g., `accounting.goldenoaks.com`)
8. Configure health check on `/_stcore/health`

---

## 15. Edge Cases & Risk Mitigation

| Scenario | Handling |
|---|---|
| Brokerage statement with reinvested dividends | Capture dividend (Schedule B) and the implicit purchase as separate ledger entries; do not double-count |
| Stock sale with both gain and proceeds | Split into Schedule B (principal proceeds) + Schedule C (gain) or E (loss) |
| Cost basis missing on brokerage statement | Flag for manual entry; do not guess |
| Statement covers period outside accounting window | Warn user; let them decide to include partial period or exclude |
| Foreign currency transactions | Flag for manual review; AI will not auto-convert |
| Wire transfers without clear counterparty | Flag as `needs_review` with low confidence |
| Refunds (negative disbursements) | Schedule D with negative amount, OR Schedule B depending on firm convention — confirm with firm |
| Trust principal vs. income split | Apply allocation per matter setup; flag if ambiguous |
| Conservator personal needs allowance | Schedule F (distributions) with subcategory `personal_needs` |
| Bank fees vs. trustee fees | Bank fees → D; Trustee/conservator fees → I — AI distinguishes by description |
| OCR errors on scanned statements | Show OCR confidence flag; staff verifies amounts against source PDF visually |
| Same-day deposits/withdrawals appearing as duplicates | Reconciler flags but does not auto-merge — staff confirms |

---

## 16. Future Versions (Out of Scope for V1)

| Feature | Version | Description |
|---|---|---|
| Petition / declaration generation | V2 | Auto-draft cover documents that reference the schedules |
| Direct e-filing integration | V2 | Submit completed accounting directly to California court e-filing systems |
| Cost-basis lookup | V2 | Pull historical cost basis for securities not on the source statement |
| Multi-period accounting | V3 | Build a single Excel covering multiple accounting periods (annual + final) |
| Plaid / financial institution APIs | V3 | Pull statements directly from institutions instead of PDF upload |
| Tax document extraction | V3 | Generate 1099 / K-1 supplemental schedules |
| Inventory & appraisal integration | V3 | Pull starting balances from prior I&A filings |
| Smart categorization learning | V3 | Learn firm-specific categorizations over time, improve confidence on repeat patterns |

---

## 17. Success Metrics

- Reduce time to draft a full accounting from days/weeks to **under 4 hours** per matter
- Categorization accuracy of **95%+** on transactions verified against ground truth (firm-completed accountings)
- Zero discrepancies between source statements and final Excel (verified via audit trail reconciliation)
- Staff adoption — target **100%** of California probate accountings drafted using the app within 60 days of launch
- Court rejection rate due to formatting/categorization errors: **0**

---

## 18. Relationship to Apps 1 & 2

This app is **fully standalone** — its own repository, Coolify deployment, database, Entra registration, and domain. It does not import code from Apps 1 or 2.

It is built using the **same architectural patterns** as Apps 1 & 2 to keep the firm's tech stack consistent and maintenance predictable.

**Patterns followed:**
- Python 3.12 + Streamlit + Coolify deployment
- Pydantic v2 for data validation
- MSAL for Entra SSO
- Settings management via `app/config.py` with `@lru_cache`
- Module organization conventions

**Code reimplemented for this app:**
- Text extraction pipeline — same library choices (pdfplumber, pytesseract, pdf2image)
- Gemini client wrappers — same structural pattern, different prompts and schemas
- PDF validation — same library choices

**New components unique to this app:**
- Two-stage Gemini pipeline (extraction + categorization)
- Cross-statement reconciliation engine
- California probate schedule logic
- Side-by-side PDF + transaction grid UI
- openpyxl-based Excel template population with audit trail
- Multi-document session management

**Why fully standalone:**
- Independent deployment lifecycle and failure isolation
- Independent Entra permissions (this app needs no SharePoint or external tool access)
- Stricter security posture justified by financial data sensitivity
- Cleaner audit trail for court-related work

---

*Prepared by Golden Oaks operations team. Questions should be directed to the project lead.*
