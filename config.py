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

# `--source foyle-sheets` reads the same six sheets live from a Google Drive folder
# (foyle.mock.sme account) instead of local xlsx. Auto-discovery lists every
# spreadsheet in the folder, so dropping a new sheet in is picked up automatically.
# YOU fills FOYLE_DRIVE_FOLDER_ID after uploading the sheets (folder URL id).
FOYLE_DRIVE_FOLDER_ID  = "1XnjN1dvsqZ1PnKvrQJp1xkbGn0Bk-7C-"  # foyle.mock.sme synthetic-sheets folder
# Sheets read + Drive folder listing. Wider than the single-sheet readonly scope, so the
# first foyle-sheets run re-opens the browser to re-consent (token.json is re-minted).
FOYLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

FOYLE_DELAY_STAGE          = "Payment Received"       # delay: invoice -> payment gap
FOYLE_REPETITION_STAGE     = "Document Re-request"    # repetition: docs collected twice
FOYLE_REWORK_STAGE         = "Placement Re-allocation"  # rework: host drop-out / company decline
FOYLE_DELAY_THRESHOLD_DAYS = 21                        # invoice->payment gap that counts as late

# Canonical Foyle stage order (drives the dashboard workflow map for this model).
FOYLE_STAGE_ORDER = [
    "Request Received", "Placement Offer", "Placement Re-allocation", "Booking Confirmed",
    "Invoice Issued", "Payment Received", "Document Re-request", "Arrival",
]

# ── Foyle Placement Tracker (real single-sheet model) ────────────────────────
# The SharePoint audit found NO master tracker and NO invoice/document/AccessNI
# sheets — the six-sheet model above is synthetic. The real workflow is one row
# per sending-org cohort with sequential pre-arrival tasks (the "Pre arrival To
# do" sheet). This block models a streamlined, dropdown-locked replacement of it.
FOYLE_TRACKER_FILE            = DATA_SYNTHETIC / "foyle_tracker.xlsx"
# Live Drive folder holding the single tracker sheet + the sheet's title to match.
# Reuses the foyle.mock.sme Drive folder + FOYLE_SCOPES (Sheets + Drive readonly).
FOYLE_TRACKER_DRIVE_FOLDER_ID = "1XnjN1dvsqZ1PnKvrQJp1xkbGn0Bk-7C-"
FOYLE_TRACKER_SHEET_TITLE     = "foyle_tracker"

# Tasks in workflow order: (status column, completion-date column | None, is_critical).
# Milestone tasks carry a real completion date; the rest are status-only (their
# event timestamp is derived from arrival purely to order the workflow map).
FOYLE_TASKS = [
    ("Enrolment",               None,                True),
    ("Documents (CV/Letter)",   "Docs Date",         True),
    ("Placement Confirmed",     "Placement Date",    True),
    ("Accommodation Assigned",  "Accom Date",        True),
    ("Arrival Info Received",   "Arrival Info Date", False),
    ("SC Deposit Received",     "Deposit Date",      False),
    ("Airport Transfer",        None,                False),
    ("Welcome Pack Sent",       None,                False),
    ("T&Cs Signed",             None,                False),
    ("Local Transport",         None,                False),
    ("Departure Details",       None,                False),
    ("Feedback Sent",           None,                False),
]
# A task counts as settled when its status is one of these (matched lower-cased);
# anything else ("Not Started", "In Progress", blank) is still open.
FOYLE_TRACKER_DONE_STATUSES = {"complete", "n/a"}

# Detection knobs for the tracker model.
FOYLE_CRITICAL_TASKS   = [t[0] for t in FOYLE_TASKS if t[2]]          # BN002 scope
FOYLE_LEADTIME_TASKS   = ["Accommodation Assigned", "Placement Confirmed"]  # BN001 scope
FOYLE_LEAD_TIME_DAYS   = 7                                            # milestone locked <N days before arrival = late
FOYLE_DEPOSIT_TASK     = "SC Deposit Received"                        # BN003: unpaid self-catering deposit

FOYLE_TRACKER_STAGE_ORDER = [t[0] for t in FOYLE_TASKS] + ["Arrival"]

# ── Messy-drive profiles (mapping-agent path) ────────────────────────────────
# The `--source messy` path reads a deliberately messy folder of xlsx files
# (overlapping seasonal forks, renamed headers, freetext statuses, irrelevant
# files) through an APPROVED mapping produced by the audit agent (audit/) and
# confirmed by a human in the dashboard's Mapping Review tab. One profile per
# SME; the core pipeline is identical for all of them — that is the point.
MAPPINGS_DIR = ROOT / "mappings"

MESSY_PROFILES = {
    "foyle": {
        "dir": DATA_SYNTHETIC / "messy_foyle",
        "markers": {
            "delay_stage": "Booking Confirmed",
            "repetition_stage": "Document Re-request",
            "rework_stage": "Placement Re-allocation",
            "delay_threshold_days": 7,
        },
        "stage_order": [
            "Request Received", "Placement Offer", "Placement Re-allocation",
            "Booking Confirmed", "Invoice Issued", "Document Re-request",
            "Pre-Arrival Logistics", "Arrival",
        ],
        "gt_mapping": DATA_SYNTHETIC / "ground_truth_mapping_foyle.json",
        "gt_bottlenecks": DATA_SYNTHETIC / "ground_truth_messy_foyle.json",
    },
    # The contrasting SME: a joinery firm's job pipeline. Different workflow
    # vocabulary, same canonical schema, zero new reader code — the
    # generalisability claim in one config block.
    "joinery": {
        "dir": DATA_SYNTHETIC / "messy_joinery",
        "markers": {
            "delay_stage": "Site Work Started",      # materials lead time
            "repetition_stage": "Re-measure",        # site measured twice
            "rework_stage": "Snagging Revisit",      # post-handover call-back
            "delay_threshold_days": 7,
        },
        "stage_order": [
            "Quote Sent", "Quote Accepted", "Re-measure", "Materials Ordered",
            "Site Work Started", "Snagging", "Snagging Revisit",
            "Invoice Sent", "Payment Received",
        ],
        "gt_mapping": DATA_SYNTHETIC / "ground_truth_mapping_joinery.json",
        "gt_bottlenecks": DATA_SYNTHETIC / "ground_truth_messy_joinery.json",
    },
}


def approved_mapping_path(profile: str) -> Path:
    return MAPPINGS_DIR / f"approved_{profile}.json"


def ui_mapping_proposal_path(profile: str) -> Path:
    return OUTPUTS / f"ui_mapping_proposal_{profile}.json"


# Audit agent knobs. Sample rows are scrubbed before they reach the API; the
# caps bound both cost and payload size.
AUDIT_SAMPLE_ROWS   = 5
AUDIT_MAX_SHEETS    = 15
AUDIT_CELL_MAX_CHARS = 80
AUDIT_MODEL_DEFAULT = "claude-opus-4-8"
