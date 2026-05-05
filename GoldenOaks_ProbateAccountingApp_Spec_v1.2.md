# GOLDEN OAKS | Probate Accounting Excel Generator — Developer Specification
**v1.2 | May 2026**

*Golden Oaks Law Firm | Confidential | May 2026*

**Changelog from v1.1:** Reviewed the firm's actual master Excel template (doc 2290, "Accounting Template"). The firm uses **a single template across all three matter types** (Conservatorship, Probate Estate, Trust Administration) — not three separate templates as v1.1 assumed. Schedule letter scheme corrected to match the template (A, B, C, E, F as standard sheets; D/G/I/K/L/P/X added per case as needed). Section 5 (Schedules), Section 8 (Excel Output), Section 11 (env vars), and Section 13 (Assets) updated. Open question flagged where Suzanne's reference doc and the template disagree on Schedule B.

**Changelog from v1.0:** Application Flow (Section 4) rewritten to mirror the firm's manual fiduciary accounting workflow (per Suzanne Graves's "Steps to Fiduciary Accounting" reference). Schedule letter scheme (Section 5) corrected to match the firm's existing master Excel templates and California statutory ordering. Categorization prompt examples (Section 6) and Reconciliation section (Section 9) updated for consistency.

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

> The flow below is built directly from the firm's manual fiduciary accounting workflow (Suzanne Graves's "Steps to Fiduciary Accounting" reference document, doc 2290). The app's job is to compress days of manual data entry, reconciliation, and schedule mapping into hours, while preserving the same checks-and-balances the attorneys rely on.

### Step 1 — New Accounting Session
Staff log in via Entra SSO and click "Start New Accounting." The app prompts for:

- **Matter name / matter ID** (linked to ActionStep)
- **Matter type** — Conservatorship / Probate Estate / Trust Administration
- **Accounting type** — First Account / Subsequent Account
- **Accounting period start date**
  - First accounting: date the fiduciary was appointed
  - Subsequent accounting: the day after the previous accounting period ended
  - Special case (Conservatorship/Probate): if a Temporary Conservator/Guardian or Special Administrator was appointed, start date is the date Temporary Letters or Letters of Special Administration were issued
- **Accounting period end date** — end of the nearest month for which statements are available
- **Beginning balance(s)** for each account
  - First accounting: pulled from Inventory & Appraisal documents
  - Subsequent accounting: pulled from "Property on Hand at End" (Schedule E) of the prior accounting

### Step 2 — Documentation Checklist & ActionStep Folder Setup
The app generates a **Documentation Tracker** based on matter type and accounting type. The tracker functions as both a status board for staff and a list of items to request from the client.

| Item | When Required | Provided By |
|---|---|---|
| Inventory & Appraisal documents | First accounting only | Client / prior filings |
| Previous accounting | Subsequent accounting only | Firm records |
| Account statements covering the full period | Always — every account | Client / institutions |
| Cancelled checks for the period | Always (where applicable) | Client / institutions |
| Credit card statements | If a credit card was used during the period | Client |
| Asset disposition documentation | Per asset sold/closed: sale price, buyer, auction commissions, payoff statements, Seller Final Closing Statement (real property) | Client |
| Cash withdrawal explanations & receipts | Per cash withdrawal | Client |
| Counterparty information for unknown recurring transactions | Per unknown payee — **no guessing** | Client |
| Fiduciary timesheets | If the client is requesting compensation (Conservatorship/Guardianship matters, or extraordinary compensation in Probate) | Client |
| Reimbursement requests | If the client is requesting reimbursements — must list date, description, amount, and supporting documentation | Client |

The tracker stays visible throughout the session and updates automatically as documents are uploaded or items are confirmed not applicable. **The session cannot move past Step 11 (Schedule Categorization) until every required item is either provided or explicitly marked "not applicable."**

In parallel, the app creates the standard ActionStep folder structure for the accounting:

```
[Matter] / Accounting / [Accounting Type] [Period Start] to [Period End] /
  ├── Inventory & Appraisals/         (first accounting only)
  ├── Prior Accounting/               (subsequent only)
  ├── [Institution] Acct. ####/       (one per account — bank, brokerage, retirement)
  ├── Cancelled Checks/
  ├── Credit Card Statements/
  ├── Asset Sales & Closures/
  ├── Cash Withdrawal Receipts/
  ├── Reimbursements/
  └── Timesheets/                     (if compensation requested)
```

Folder names follow the firm convention (e.g., `First Account Current 1/1/2024 to 8/6/2025`).

### Step 3 — Multi-Document Upload
- Drag-and-drop zone accepts multiple PDFs at once
- Each uploaded file is auto-tagged: institution, account type, last 4 of account number, statement period
- Files are routed to the appropriate ActionStep subfolder created in Step 2
- Staff can re-tag if AI mis-detects
- App displays a summary against the documentation tracker — e.g., "Bank of America 8503: 11 of 12 monthly statements received; September statement missing"

### Step 4 — Text Extraction
- Each PDF is run through the extraction pipeline (pdfplumber → OCR fallback)
- Brokerage statements use pdfplumber's table-finding logic
- Status: `pending_extraction` → `extracted`

### Step 5 — AI Transaction Extraction
For each statement, Gemini extracts every transaction. Per the firm's required input format, every transaction must include:

- Date
- Description **as it appears on the statement** (no interpretation at this stage)
- Last 4 digits of the account number
- Check number (if applicable)
- Amount, with credits and debits captured in **separate fields** (the firm's spreadsheets keep them in separate columns)
- Type tag (debit / credit / dividend / interest / fee / transfer / trade / other)
- Running balance (if shown)
- Source page number

Returns structured JSON validated by Pydantic. Status: `extracted` → `pending_reconciliation`.

### Step 6 — Per-Account Reconciliation (gating step)
**Before any categorization happens, every account must reconcile.** This mirrors the firm's manual rule that a missing transaction is detected here, not after schedules are built.

For each account the app:

1. Sums all credits and all debits separately
2. Computes: `beginning balance + total credits − total debits = expected ending balance`
3. Compares to the ending balance printed on the statement
4. **If they match:** mark the account `reconciled`
5. **If they don't match:** flag the account `reconciliation_failed` with the discrepancy amount, surface the most likely missing date ranges, and block progress until resolved

Staff can re-upload missing pages, manually add a transaction, or mark a flagged item as resolved. Multi-month accounts also have ending balance N = beginning balance N+1 verified across statement boundaries.

Status: `pending_reconciliation` → `reconciled` (per account).

### Step 7 — Description Normalization
Once accounts are reconciled, the app cleans up descriptions so like-kind transactions can be filtered together later (mirrors the attorney's instruction to simplify descriptions and ensure like-kind transactions share the same wording):

- Gemini groups transactions with substantively identical purposes (e.g., "SCE PYMT 0123", "SOUTHERN CALIF EDISON BILL", "SCE WEB PAYMENT" → all become "Southern California Edison")
- Staff reviews proposed groupings in a side-by-side panel and approves, edits, or splits them
- Original statement description is preserved in the audit trail; the simplified description is what carries forward to schedule mapping

### Step 8 — Asset Disposition & Closure Review
The app surfaces every account closure and asset sale detected during the period and prompts for the supporting facts the attorney's checklist requires:

- **Account closures:** where did the funds go (transferred to which account, distributed to whom, etc.)
- **Asset sales:** sale price, buyer, auction commissions (if applicable), payoff statements, and — for real property — the Seller Final Closing Statement
- **Cost basis:** captured per sale (where available) so principal proceeds (Schedule A, Miscellaneous Receipts) and any gain or loss can be split correctly. Note that gains/losses do not have dedicated sheets in the standard template — losses trigger an ad-hoc Schedule D and gains either fold into Schedule A or trigger an ad-hoc Gains sheet that staff confirms by case (see Open Question in Section 5).

Items here cannot be skipped — they drive Schedules B (Gains), D (Losses), and F (Additional Property Received).

### Step 9 — Unknown Transaction & Cash Withdrawal Resolution
The app builds a **Client Questions list** for staff to send to the client:

- Recurring transactions to unidentified counterparties (e.g., "Monthly Zelle of $400 to 'Pedro Martinez' — please confirm purpose")
- Cash withdrawals without an attached receipt or explanation
- Wire transfers without a clear counterparty
- Any transaction Gemini flagged as ambiguous

**Per firm rule: do not guess. Do not categorize until the client has answered.** Staff record the client's response in the app, attach receipts where applicable, and the transaction's `notes` field is updated for the audit trail.

### Step 10 — Reimbursements & Compensation Intake
If the matter requires it, staff enter:

- **Reimbursement requests** — date, description, amount, supporting documentation upload (one row per request)
- **Fiduciary timesheets** — for Conservatorships/Guardianships, or for Probate matters seeking extraordinary compensation

These items are added to the transaction set as additional rows so they flow into the schedules alongside the extracted statement transactions.

### Step 11 — AI Schedule Categorization
With every account reconciled, every description normalized, every disposition documented, and every unknown resolved, the app sends the full transaction set to Gemini with:

- Matter type
- The firm's schedule definitions (see Section 5)
- **Internal-transfer rule:** do not assign to a schedule any transfer between two accounts held in the name of the estate/trust/conservatorship — this is simply a change in the manner the asset is held, not income or an expense. Transfers to a non-matter account (e.g., trustee's personal account) **are** categorized — typically as Schedule G (Distribution), or as a loan or reimbursement depending on the facts.
- Confidence requirement: every transaction returns `high` / `medium` / `low` plus reasoning for medium/low

Status: `reconciled` → `pending_review`.

### Step 12 — Side-by-Side Verification
Staff verify every transaction with the source statement visible. See Section 7 for full UI spec. The verification UI supports filtering by like-description and bulk-approve once a pattern is confirmed (mirrors the attorney's "filter feature in Excel" step in the manual workflow).

### Step 13 — Excel Generation
- Loads the firm's master template for the matter type
- Populates each schedule sheet using the firm's schedule letter scheme (Section 5)
- Preserves all formulas, totals, and formatting
- Adds a hidden `_AuditTrail` sheet mapping every transaction to source file + page + verifier + timestamp
- Saves with the auto-generated filename pattern (Section 8)

Status: `pending_review` → `completed`.

---

## 5. Firm's Accounting Template — Schedules & Sheet Structure

The firm uses a **single master Excel template** (doc 2290, "Accounting Template") for all three matter types. Schedule letters and column structures are fixed; subcategory labels within each schedule are case-specific and edited per matter (e.g., a probate estate accounting will replace "Residential Facility/Caregiver" with funeral or estate-administration categories).

### Sheets in the Template (8 total)

| # | Sheet Name | Purpose |
|---|---|---|
| 1 | **Working Balance** | Master summary. Pulls totals from the schedule sheets via cross-sheet formulas. CHARGES = POH Begin + Receipts (A) + Additional Property (F). CREDITS = POH End + Disbursements (C). |
| 2 | **Statements** | Internal documentation tracker — one column block per account. Tracks Acct #, Statement Date, Need?, Have Stmt?, Have Copy of Checks?, Input? — exactly the tracker Suzanne's reference describes. Three account blocks side-by-side in the master template; staff add more blocks as needed. |
| 3 | **Bank Statement Transactions** | The working ledger. Per-account block with columns: Date, Description, Acct Number, Check Number, Copy of Chk, Debit, Credit, Additional Info. Each block has a TOTALS row and a balance check (`BALANCED= YES`). This is where Step 5 (extraction) and Step 6 (per-account reconciliation) outputs land. |
| 4 | **POH @ Beginning Schedule B** | Property on Hand at Beginning. Cash/Cash Equivalents and Non-Cash Assets sections, each with Description / Market Value / Carry Value columns. |
| 5 | **POH @ End Schedule E** | Property on Hand at End. Same structure as POH Beginning. |
| 6 | **Schedule A** | Receipts. Subcategorized: Interest; Pensions, Annuities, and Other Regular Periodic Payments; Miscellaneous Receipts. |
| 7 | **Schedule C** | Disbursements. Subcategorized (conservatorship-flavored, edited per case): Residential Facility/Caregiver; Living Expenses; Medical Expenses; Legal & Related Professional Expenses; Insurance; Miscellaneous. |
| 8 | **Schedule F** | Additional Property Received During the Accounting Period. |

### Schedule Letter Mapping (template ground truth)

| Schedule | Name | Standard sheet? | Notes |
|---|---|---|---|
| **A** | Receipts | ✅ Yes | Subcategories above |
| **B** | Property on Hand at Beginning | ✅ Yes | Cash + Non-Cash, with Market Value and Carry Value columns |
| **C** | Disbursements | ✅ Yes | Subcategories above; this is also where professional fees and bank fees go (no separate Schedule P sheet in the template) |
| **E** | Property on Hand at End | ✅ Yes | Mirrors Schedule B structure |
| **F** | Additional Property Received | ✅ Yes | Description + Carry Value |

### Schedules Added Per Case (Not in Standard Template)

Per Suzanne's reference doc, additional schedules are created as new sheets when the case requires them. The app must be able to add these dynamically when transactions warrant:

| Schedule | Name | When Needed |
|---|---|---|
| **D** | Losses on Sales During the Period | When realized losses exist |
| **G** | Distributions to Beneficiaries / Conservatee / Minor | When distributions are made (more common in trust/probate than conservatorship) |
| **I** | Net Income from Trade or Business | When the matter operates a business |
| **K** | Change in Assets | When assets change character/holding form non-trivially |
| **L** | Net Loss from Trade or Business | When the matter operates a business at a loss |
| **P** | Professional Fees | If the firm decides to break professional fees out of Schedule C for a particular case |
| **X** | Cash Reconciliation | If a separate reconciliation schedule is required |

When the AI categorizer assigns a transaction to one of these, the app:
1. Adds a new sheet to the workbook with appropriate column headers and totals row
2. Adds a corresponding line to the Working Balance summary
3. Logs to the audit trail that an ad-hoc schedule was added

### Internal Transfer Rule
A transfer from one account held in the name of the estate/trust/conservatorship to another account also held in that name is **not** transferred to any schedule — it is simply a change in the manner the asset is held, not income or an expense. Internal transfers are retained in the audit trail and on the Bank Statement Transactions sheet, but excluded from the schedule sheets.

A transfer from a matter account to a non-matter account (e.g., trustee's personal account) **is** categorized — typically Schedule G (Distributions, ad-hoc), or treated as a loan or reimbursement depending on the facts. If unclear, the AI flags it for staff/attorney review rather than guessing.

### Matter-Type Specific Variations (within the same template)

The same template is used for all three matter types; the differences show up in:

**Conservatorships (Probate Code § 2620):**
- Schedule C subcategories stay close to the template defaults (Residential Facility, Living, Medical, Insurance, etc.)
- Personal needs allowance broken out within Schedule G when applicable
- Bond information often added in Working Balance header
- Court order references on disbursements

**Probate Estates (Probate Code § 1061):**
- Schedule C subcategories edited per case (e.g., Funeral Expenses, Creditor Claims, Estate Administration)
- Inventory & Appraisal cross-references required (drives Schedule B)
- Creditor claim payments tracked separately within Schedule C

**Trust Administration (Probate Code § 16063, § 1064):**
- Schedule C subcategories edited per case (trust expenses, trustee-administered costs)
- Principal vs. Income distinction required — typically handled by splitting subcategories within A and C, or by adding parallel principal/income sheets when significant
- Trustee compensation may have separate principal/income allocation

### Open Question — Schedule B Letter Discrepancy

Suzanne's reference doc lists "Gains on Sales During the Period (Schedule B)" — but the firm's actual template uses Schedule B for "Property on Hand at Beginning" and has no dedicated Gains schedule. **The template wins for code purposes** (the app must produce a file that opens correctly in the firm's existing workflow), but this should be confirmed with Suzanne before launch — possibly the reference doc was written from memory or from a non-firm template. If the firm wants to standardize on Suzanne's letter scheme, the template needs to be updated first.

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
      "schedule": "A",
      "subcategory": "Interest",
      "confidence": "high",
      "reasoning": "Standard bank interest payment — Schedule A (Receipts), Interest section"
    },
    {
      "transactionId": "txn_002",
      "schedule": "C",
      "subcategory": "Living Expenses",
      "confidence": "high",
      "reasoning": "Utility bill — Schedule C (Disbursements), Living Expenses section"
    },
    {
      "transactionId": "txn_003",
      "schedule": "C",
      "subcategory": "Legal & Related Professional Expenses",
      "confidence": "high",
      "reasoning": "Attorney fee payment — Schedule C, Legal & Related Professional Expenses section"
    },
    {
      "transactionId": "txn_004",
      "schedule": "needs_review",
      "subcategory": null,
      "confidence": "low",
      "reasoning": "Description 'WIRE OUT - REFERENCE 4421' is ambiguous — could be a Distribution (ad-hoc Schedule G), an internal transfer (excluded), or a loan to the trustee"
    }
  ]
}
```

The `subcategory` value must match an existing subcategory header on the target schedule sheet, or the categorizer must flag for staff to confirm a new subcategory name.

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

### Single Master Template
The app uses a **single firm-wide master Excel template** (doc 2290) for all matter types. The template's existing 8 sheets, formulas, and totals must be preserved. The app does not regenerate the workbook from scratch — it loads the master, populates predefined ranges, and saves a new file.

### Sheets Populated by the App

| Sheet | What the App Writes | Notes |
|---|---|---|
| Working Balance | Matter name (A1), case number (A2), accounting type (B4), fiduciary name (B5), period (B7) | The rest is cross-sheet formulas — leave them alone |
| Statements | One column block per account: Acct # (last 4), monthly statement dates, Need? / Have Stmt? / Have Copy of Checks? / Input? indicators | Three blocks fit side-by-side; insert additional blocks for accounts beyond three |
| Bank Statement Transactions | Per-account block: header row with bank name; data rows with Date / Description / Acct Number / Check Number / Copy of Chk / Debit / Credit / Additional Info; TOTALS row with `=SUM()` formulas; balance verification row | One block per account, separated by blank rows |
| POH @ Beginning Schedule B | Cash and Cash Equivalents rows (Description, Market Value, Carry Value); Non-Cash Assets rows (same columns); subtotals via existing formulas | Pulled from I&A documents (first accounting) or prior accounting's Schedule E (subsequent) |
| POH @ End Schedule E | Same structure as Schedule B but for end-of-period values | Mirrors Schedule B |
| Schedule A | Three subcategory blocks: Interest; Pensions, Annuities, and Other Regular Periodic Payments; Miscellaneous Receipts. Each with Date / Description / Amount columns | Subcategory totals roll up to "Total Schedule A" via existing formulas |
| Schedule C | Six subcategory blocks: Residential Facility/Caregiver; Living Expenses; Medical Expenses; Legal & Related Professional Expenses; Insurance; Miscellaneous. Each with Date / Account / Description / Check / Amount columns | Subcategory labels editable per case; rolled-up total via existing formulas at F98 (or wherever the final sum lands after edits) |
| Schedule F | Date / Description / Carry Value rows | Total rolls up via existing formula |

### Subcategory Handling
The AI categorizer must assign each transaction to **both** a schedule letter AND a subcategory header. If the proposed subcategory doesn't exist on the target sheet for the active matter:
- For Schedule A and Schedule C — flag for staff to either map to an existing subcategory or confirm adding a new one (e.g., a probate estate accounting will add "Funeral Expenses" to Schedule C)
- For Schedules B, E, F — no subcategories; just assign rows directly

### Ad-Hoc Schedules (D, G, I, K, L, P, X)
When the AI categorizes any transaction to a schedule not in the standard template, the app:
1. Prompts staff to confirm the schedule should be added for this matter
2. On confirmation, inserts a new sheet using a built-in skeleton (header rows, columns, totals row with `=SUM()` formula)
3. Adds a corresponding line to the Working Balance sheet (CHARGES side for receipts/property; CREDITS side for disbursements/distributions/losses) with a cross-sheet formula
4. Logs the addition to the audit trail

Built-in skeletons for D, G, I, K, L, P, X live in the codebase (not in the template file) so the master template stays clean.

### Generation Flow
1. Load the master template (path from `TEMPLATE_PATH` env var)
2. Write matter metadata to the Working Balance header
3. Populate Statements tracker from the documentation tracker state
4. Populate Bank Statement Transactions from the verified transactions, grouped by account
5. Populate Schedules A, B, C, E, F from verified transactions per their categorization
6. For any ad-hoc schedule (D, G, I, K, L, P, X) with verified transactions, inject the new sheet and update Working Balance
7. Append a hidden `_AuditTrail` sheet (see below)
8. Run formula recalculation (preserves existing formulas, refreshes computed values)
9. Save with auto-generated filename

### File Naming
```
[MatterName]_Accounting_[PeriodStart]_to_[PeriodEnd]_[GeneratedDate].xlsx
```
Example: `Smith_Conservatorship_Accounting_2025-01-01_to_2025-12-31_2026-05-03.xlsx`

### Audit Trail Sheet
Hidden by default. Columns:
- Schedule
- Subcategory
- Row in schedule sheet
- Source File
- Source Page
- Original Description (as printed on statement)
- Normalized Description (post-Step 7)
- Amount
- AI Schedule Suggestion
- AI Subcategory Suggestion
- AI Confidence
- Final Schedule (after verification)
- Final Subcategory (after verification)
- Edited By Staff?
- Verified By
- Verification Timestamp

This sheet is critical for partner review and any future court inquiry.

### Template Configuration File
The mapping between schedule letters, sheet names, and cell ranges is held in a single JSON config rather than per-matter-type configs. Example structure:

```json
{
  "templatePath": "templates/2290_Accounting_Template.xlsx",
  "sheets": {
    "workingBalance": { "sheet": "Working Balance", "matterNameCell": "A1", "caseNumberCell": "A2", "accountingTypeCell": "B4", "fiduciaryNameCell": "B5", "periodCell": "B7" },
    "statements": { "sheet": "Statements", "blockStartColumns": ["B", "I", "P"], "headerRow": 2, "dateStartRow": 5 },
    "bankTransactions": { "sheet": "Bank Statement Transactions", "blockHeaderRowOffset": 0, "dataStartRowOffset": 1, "totalsRowOffset": 9 },
    "scheduleB": { "sheet": "POH @ Beginning Schedule B", "cashStartRow": 9, "nonCashStartRow": 16 },
    "scheduleE": { "sheet": "POH @ End Schedule E", "cashStartRow": 10, "nonCashStartRow": 17 },
    "scheduleA": { "sheet": "Schedule A ", "subcategories": { "Interest": { "startRow": 9 }, "Pensions, Annuities, and Other Regular Periodic Payments": { "startRow": 21 }, "Miscellaneous Receipts": { "startRow": 38 } } },
    "scheduleC": { "sheet": "Schedule C", "subcategories": { "Residential Facility/Caregiver": { "startRow": 10 }, "Living Expenses": { "startRow": 29 }, "Medical Expenses": { "startRow": 49 }, "Legal & Related Professional Expenses": { "startRow": 66 }, "Insurance": { "startRow": 79 }, "Miscellaneous": { "startRow": 92 } } },
    "scheduleF": { "sheet": "Schedule F", "dataStartRow": 7 }
  },
  "adHocSchedules": {
    "D": { "label": "Losses on Sales During the Period", "addToWorkingBalance": "CREDITS" },
    "G": { "label": "Distributions to Beneficiaries / Conservatee / Minor", "addToWorkingBalance": "CREDITS" },
    "I": { "label": "Net Income from Trade or Business", "addToWorkingBalance": "CHARGES" },
    "K": { "label": "Change in Assets", "addToWorkingBalance": "CREDITS" },
    "L": { "label": "Net Loss from Trade or Business", "addToWorkingBalance": "CREDITS" },
    "P": { "label": "Professional Fees", "addToWorkingBalance": "CREDITS" },
    "X": { "label": "Cash Reconciliation", "addToWorkingBalance": null }
  }
}
```

The exact row numbers must be verified against the master template before implementation — they shift when subcategory blocks expand.

---

## 9. Reconciliation

Reconciliation in this app happens in two passes. The first pass — per-account — runs as a hard gate at Step 6 of the application flow, before any schedule categorization. The second pass — cross-statement — runs alongside categorization to catch issues the per-account check can't see.

### Pass 1 — Per-Account Reconciliation (gating, runs at Step 6)
For each account in the period:

1. Sum all credits and all debits separately
2. Compute: `beginning balance + total credits − total debits = expected ending balance`
3. Compare to the ending balance printed on the statement
4. **Match:** mark account `reconciled` and allow it to advance
5. **Mismatch:** flag account `reconciliation_failed` with the discrepancy amount; surface likely missing date ranges; block progress until resolved

This mirrors the firm's manual rule: a missing transaction must be discovered here, not after schedules have been built. The session cannot advance to description normalization or schedule categorization until every account reconciles.

### Pass 2 — Cross-Statement Checks (runs during/after categorization)

**Internal transfer matching.** When two transactions match across two matter-owned accounts (one credit, one debit, same amount, same or adjacent date), they are auto-grouped as `internal_transfer` and excluded from final schedules per the firm's transfer rule. Retained in the audit trail. Staff can override and treat them separately if the facts warrant.

**Duplicate detection.** Same amount + same date + same description on the same account is flagged as a potential duplicate. Reconciler does not auto-merge — staff confirms.

**Statement-boundary balance verification.** Beginning balance on statement N+1 must equal ending balance on statement N for each account. Mismatches are flagged with the discrepancy amount and a note about likely causes (e.g., timing differences across statement-cycle dates).

**Period coverage.** App verifies the uploaded statements cover the full accounting period without gaps. Flags any account with missing months. Drives back into the Documentation Tracker (Step 2) so staff know what to request from the client.

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
| `app/gemini_normalizer.py` | Description normalization Gemini wrapper (Step 7 of flow) |
| `app/gemini_categorizer.py` | Stage 2 — schedule categorization Gemini wrapper |
| `app/models.py` | Pydantic models — `Transaction`, `Statement`, `Categorization`, `AccountingSession`, `DocumentationItem`, `ClientQuestion`, `AssetDisposition`, `ReimbursementRequest` |
| `app/reconciler.py` | Per-account reconciliation (gating) and cross-statement duplicate / transfer / balance reconciliation |
| `app/doc_tracker.py` | Documentation checklist state machine — drives Step 2 tracker and gates Step 11 |
| `app/actionstep_client.py` | ActionStep API client — creates the accounting folder structure and routes uploaded files into the correct subfolders |
| `app/schedules.py` | Firm's schedule definitions (A, B, C, D, E, F, G, I, K, L, P, X), matter-type variants, internal-transfer rule |
| `app/excel_writer.py` | openpyxl-based template population with audit trail |
| `app/template_config.py` | Loads/validates the JSON template mapping config (single template) |
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
| `TEMPLATE_PATH` | Path to the firm's single master Excel template (doc 2290) |
| `TEMPLATE_MAPPING_PATH` | Path to JSON file defining sheet/cell mappings (single config — see Section 8) |
| `ACTIONSTEP_BASE_URL` | ActionStep API base URL for folder creation and file routing |
| `ACTIONSTEP_CLIENT_ID`, `ACTIONSTEP_CLIENT_SECRET`, `ACTIONSTEP_REFRESH_TOKEN` | ActionStep OAuth credentials |
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
| Master Excel template (single template for all three matter types — doc 2290) | .xlsx | ✅ Provided | Single template covers Conservatorship / Probate / Trust |
| Template mapping documentation | JSON config | ⏳ Pending | Cell/row mapping derived from Section 8; verify exact rows against the live template before implementation |
| Sample completed accounting | .xlsx + source PDFs | ⏳ Pending | For testing categorization accuracy end-to-end |
| Categorization rules / firm conventions | Document or notes | ⏳ Optional | Any firm-specific schedule treatment beyond standard probate code |
| New Entra app registration | Tenant + client config | ⏳ Pending | Separate from Apps 1 & 2 |
| Gemini / Vertex AI key | Credentials | ⏳ Pending | Vertex AI strongly preferred for this app |
| Coolify infrastructure access | Same instance, separate deployment | ⏳ Pending | App 3 deploys as its own Coolify resource |
| New domain or subdomain | e.g., `accounting.goldenoaks.com` | ⏳ Pending | Distinct from Apps 1 & 2 |
| Sample bank, brokerage, credit card, retirement statements | PDFs | ⏳ Optional | Recommended for testing extraction accuracy |
| ActionStep API credentials (OAuth client + refresh token) | Credentials | ⏳ Pending | Needed for Step 2 folder creation and file routing |
| Source workflow reference: "Steps to Fiduciary Accounting" (doc 2290) | .docx | ✅ Provided | Drives Section 4 application flow |

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
3. Upload the master Excel template to the templates volume
4. Set environment variables
5. Mount persistent volumes for `/app/data` and `/app/templates`
6. Enable HTTPS via Coolify Let's Encrypt
7. Set up distinct custom domain (e.g., `accounting.goldenoaks.com`)
8. Configure health check on `/_stcore/health`

---

## 15. Edge Cases & Risk Mitigation

| Scenario | Handling |
|---|---|
| Brokerage statement with reinvested dividends | Capture dividend (Schedule A, Miscellaneous Receipts) and the implicit purchase as separate ledger entries; do not double-count |
| Stock sale with both gain and proceeds | Principal proceeds → Schedule A (Miscellaneous Receipts); the gain itself triggers an ad-hoc Schedule D (loss) or new Gains schedule — flag for staff to confirm subcategory naming since the standard template has no Gains sheet |
| Cost basis missing on brokerage statement | Flag for manual entry; do not guess |
| Statement covers period outside accounting window | Warn user; let them decide to include partial period or exclude |
| Foreign currency transactions | Flag for manual review; AI will not auto-convert |
| Wire transfers without clear counterparty | Flag as `needs_review` with low confidence |
| Refunds (negative disbursements) | Schedule C with negative amount, OR Schedule A (Miscellaneous Receipts) depending on firm convention — confirm with firm |
| Trust principal vs. income split | Apply allocation per matter setup; flag if ambiguous |
| Conservator personal needs allowance | Schedule C → Living Expenses (default), unless the case requires a separate ad-hoc Schedule G (Distributions) |
| Bank fees vs. trustee fees | Bank fees → Schedule C (Miscellaneous); Trustee/conservator/professional fees → Schedule C (Legal & Related Professional Expenses), unless the case breaks them out into an ad-hoc Schedule P |
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
