"""Central config. Everything that might change lives here — no magic strings elsewhere.

To point the pipeline at a new data source, edit COLUMN_MAP and the SHEET_* values here
and nothing else.
"""

from pathlib import Path

ROOT           = Path(__file__).parent
DATA_SYNTHETIC = ROOT / "data" / "synthetic"
DATA_REAL      = ROOT / "data" / "real"
OUTPUTS        = ROOT / "outputs"
CHROMA_PATH    = OUTPUTS / "chroma"
CREDS_PATH     = ROOT / "credentials" / "google_oauth.json"
TOKEN_PATH     = ROOT / "credentials" / "token.json"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Google Sheets — YOU fills this in after the mock environment is set up.
SHEET_ID    = "1I0W-8gVuge4fdqa9apldacDkQjbwmhJ6h-tqf-DiN78"
SHEET_TAB   = "Sheet1"
SHEET_RANGE = "A:Z"

# Column mapping — maps source column names -> canonical field names.
# Update this when real Foyle data arrives without touching any other code.
COLUMN_MAP = {
    "case_id":   ["Booking Ref", "Case ID", "Reference", "ID"],
    "activity":  ["Stage", "Activity", "Step", "Status Label"],
    "timestamp": ["Date", "Updated", "Completed Date", "Timestamp"],
    "actor":     ["Handled By", "Assigned To", "Staff", "Owner"],
    "status":    ["Status", "State", "Outcome"],
}

CHUNK_SIZE    = 400
CHUNK_OVERLAP = 80
SPACY_MODEL   = "en_core_web_sm"
SCRUB_ENTITIES = {"PERSON", "ORG", "GPE", "LOC", "FAC", "NORP"}

# Output artefacts
EVENT_LOG_PATH = OUTPUTS / "event_log.parquet"
RECORDS_PATH   = OUTPUTS / "records.jsonl"
CHROMA_COLLECTION = "sme_ops"

# ── Detection ────────────────────────────────────────────────────────────────
# Marker stages for the three bottleneck patterns (matched case-insensitively).
# Domain: Foyle International student-placement workflow (host families / CVs /
# invoices). Stage labels reflect the real SMMP process; detection mechanics are
# unchanged (delay = temporal gap into marker; repetition/rework = marker present).
DELAY_STAGE         = "Booking Confirmed"        # delay: host-family matching takes 8-14 days vs 1-2
REPETITION_STAGE    = "Document Re-request"      # repetition: student docs (CV/ML/consent) re-keyed
REWORK_STAGE        = "Placement Re-allocation"  # rework: host fell through, student re-placed
DELAY_THRESHOLD_DAYS = 7                          # gap into DELAY_STAGE that counts as delayed

# Canonical stage order (the Foyle placement workflow, start -> finish). Drives the
# workflow-map layout in the dashboard.
STAGE_ORDER = [
    "Request Received", "Placement Offer", "Placement Re-allocation", "Booking Confirmed",
    "Invoice Issued", "Document Re-request", "Pre-Arrival Logistics", "Arrival",
]

# UI export
UI_CASES_PATH    = OUTPUTS / "ui_cases.json"
UI_WORKFLOW_PATH = OUTPUTS / "ui_workflow.json"

# ── Foyle multi-sheet model (Phase B) ────────────────────────────────────────
# The `--source foyle` path reads six wide staff-maintained sheets + three emails
# (data/synthetic/foyle/) and derives an event log from them. Unlike the single
# event-log sheet, the genuine timestamps here are invoice->payment, so the delay
# marker is Payment Received (not Booking Confirmed). Its own marker set keeps the
# old single-sheet path + tests untouched.
FOYLE_DIR              = DATA_SYNTHETIC / "foyle"
FOYLE_DELAY_STAGE          = "Payment Received"       # delay: invoice -> payment gap
FOYLE_REPETITION_STAGE     = "Document Re-request"    # repetition: docs collected twice
FOYLE_REWORK_STAGE         = "Placement Re-allocation"  # rework: host drop-out / company decline
FOYLE_DELAY_THRESHOLD_DAYS = 21                        # invoice->payment gap that counts as late

# Canonical Foyle stage order (drives the dashboard workflow map for this model).
FOYLE_STAGE_ORDER = [
    "Request Received", "Placement Offer", "Placement Re-allocation", "Booking Confirmed",
    "Invoice Issued", "Payment Received", "Document Re-request", "Arrival",
]
