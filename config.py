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
        # markers are EVAL-ONLY: the baseline detector (detect_generic) the
        # dynamic detector is scored against. The pipeline itself runs
        # detection.dynamic.detect_dynamic, which needs no markers.
        "markers": {
            "delay_stage": "Booking Confirmed",
            "repetition_stage": "Document Collection",
            "rework_stage": "Placement Offer",
            "delay_threshold_days": 7,
        },
        "stage_order": [
            "Request Received", "Placement Offer", "Booking Confirmed",
            "Invoice Issued", "Document Collection",
            "Pre-Arrival Logistics", "Arrival",
        ],
        "gt_mapping": DATA_SYNTHETIC / "ground_truth_mapping_foyle.json",
        "gt_bottlenecks": DATA_SYNTHETIC / "ground_truth_messy_foyle.json",
        # Drives the dashboard's SME-adaptive branding (header wordmark/context).
        "ui": {"brand": "Foyle International", "context": "Placement Ops",
               "domain": "international education placement", "initials": "FI"},
        # Demo cost model for the dashboard's "what this costs you" figures:
        # a stalled booking ties up a host + risks the placement fee; every
        # duplicated collection or re-match is staff hours.
        "costs": {"currency": "£", "delay_day_cost": 35,
                  "repetition_event_cost": 90, "rework_loop_cost": 250},
        # Action-layer settings: the labels, monetary assumptions and working
        # rhythm the generic action/intervention layer reads. Nothing here is
        # code — a new SME supplies its own block and the core is untouched.
        "actions": {
            "workflow": "Placement pipeline", "case_noun": "booking",
            "default_owner": "Placement coordinator",
            "impact": {"avg_case_value": 1_850, "hourly_rate": 26,
                       "hours_per_repetition": 2.0, "hours_per_rework": 5.0,
                       "owner_load_limit": 6},
        },
        "case_rules": {
            "stage_sla_days": {"Request Received": 5, "Placement Offer": 7,
                               "Booking Confirmed": 7, "Invoice Issued": 30,
                               "Document Collection": 10},
            "default_stalled_days": 21,
            "terminal_stages": ["Arrival"],
            "revenue_stages": ["Invoice Issued"],
            "owner_load_limit": 6,
            "key_person_min_share": 0.6,
            "key_person_min_events": 6,
            "min_affected": 1,
        },
    },
    # The contrasting SME: a joinery firm's job pipeline. Different workflow
    # vocabulary, same canonical schema, zero new reader code — the
    # generalisability claim in one config block.
    "joinery": {
        "dir": DATA_SYNTHETIC / "messy_joinery",
        # markers are EVAL-ONLY — see the foyle note above.
        "markers": {
            "delay_stage": "Site Work Started",      # materials lead time
            "repetition_stage": "Site Survey",       # site measured twice
            "rework_stage": "Site Work Started",     # post-handover call-back
            "delay_threshold_days": 7,
        },
        "stage_order": [
            "Quote Sent", "Quote Accepted", "Site Survey", "Materials Ordered",
            "Site Work Started", "Snagging", "Invoice Sent", "Payment Received",
        ],
        "gt_mapping": DATA_SYNTHETIC / "ground_truth_mapping_joinery.json",
        "gt_bottlenecks": DATA_SYNTHETIC / "ground_truth_messy_joinery.json",
        "ui": {"brand": "McCrossan Joinery", "context": "Job Ops",
               "domain": "joinery & fit-out", "initials": "MJ"},
        # A wasted trip is half a fitter-day; a stalled job is idle capacity.
        "costs": {"currency": "£", "delay_day_cost": 60,
                  "repetition_event_cost": 180, "rework_loop_cost": 320},
        "actions": {
            "workflow": "Job pipeline", "case_noun": "job",
            "default_owner": "Workshop manager",
            "impact": {"avg_case_value": 5200, "hourly_rate": 32,
                       "hours_per_repetition": 3.0, "hours_per_rework": 8.0,
                       "owner_load_limit": 4},
        },
        "case_rules": {
            "stage_sla_days": {"Quote Sent": 7, "Quote Accepted": 5,
                               "Materials Ordered": 14, "Snagging": 7,
                               "Invoice Sent": 30},
            "default_stalled_days": 21,
            "terminal_stages": ["Payment Received"],
            "revenue_stages": ["Payment Received"],
            "owner_load_limit": 4,
            "key_person_min_share": 0.6,
            "key_person_min_events": 6,
            "min_affected": 1,
        },
    },
    # SME #3: a 12-20 person professional-services firm running a lead-to-cash
    # workflow on spreadsheets. The most commercially recognisable of the three
    # — money, capacity and client-delivery risk are all explicit in the data —
    # and it required no new reader, detector or action code: this config block,
    # a synthetic drive and an approved mapping.
    "advisory": {
        "dir": DATA_SYNTHETIC / "messy_advisory",
        # markers are EVAL-ONLY — the baseline detector, as for the others.
        "markers": {
            "delay_stage": "Proposal",          # proposals stuck in internal approval
            "repetition_stage": "Client Review",  # review loops
            "rework_stage": "Delivery",         # work reopened after review
            "delay_threshold_days": 7,
        },
        "stage_order": [
            "Lead", "Qualification", "Proposal", "Won", "Onboarding",
            "Delivery", "Client Review", "Invoice", "Paid",
        ],
        "gt_mapping": DATA_SYNTHETIC / "ground_truth_mapping_advisory.json",
        "gt_bottlenecks": DATA_SYNTHETIC / "ground_truth_messy_advisory.json",
        "ui": {"brand": "Northstar Advisory", "context": "Lead-to-cash",
               "domain": "professional services consultancy", "initials": "NA"},
        # A consultant day is the unit of everything here: a stalled engagement
        # is an unbilled day, a repeated review is a day redone, reopened
        # delivery is several.
        "costs": {"currency": "£", "delay_day_cost": 120,
                  "repetition_event_cost": 420, "rework_loop_cost": 1400},
        "actions": {
            "workflow": "Lead-to-cash", "case_noun": "engagement",
            "default_owner": "Managing partner",
            "owner_by_category": {"case_action": "Engagement lead",
                                  "data_quality": "Practice manager",
                                  "process_intervention": "Managing partner"},
            "due_days": {"case_action": 2, "data_quality": 7,
                         "process_intervention": 21},
            "impact": {"avg_case_value": 18_000, "hourly_rate": 95,
                       "hours_per_repetition": 4.0, "hours_per_rework": 12.0,
                       "minutes_per_messy_cell": 0.75, "owner_load_limit": 4},
        },
        # The operational rhythm of a consultancy: leads go cold in days,
        # proposals in a week, invoices in a month.
        "case_rules": {
            "stage_sla_days": {"Lead": 3, "Qualification": 5, "Proposal": 7,
                               "Won": 5, "Onboarding": 7, "Delivery": 21,
                               "Client Review": 10, "Invoice": 30},
            "default_stalled_days": 21,
            "terminal_stages": ["Paid"],
            "revenue_stages": ["Paid"],
            "owner_load_limit": 4,
            "key_person_min_share": 0.6,
            "key_person_min_events": 6,
            "min_affected": 1,
        },
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

# ── RAG diagnosis (resolution corpus + diagnosis agent) ──────────────────────
# The past-resolution knowledge base lives in its OWN Chroma collection so that
# ingest.py's reset_collection() (which wipes only sme_ops) never touches it.
RESOLUTIONS_COLLECTION = "sme_resolutions"
DIAGNOSE_MODEL_DEFAULT = "claude-opus-4-8"
# Gate-2 (fix approval) decisions are appended by the React dashboard's API
# (hitl-react/api/main.py -> _DECISIONS_LOG); the agent's resume step reads
# them from there. The legacy Streamlit log (hitl-interface/logs/) is NOT the
# live one — pass --decisions to pipeline.agent to override if needed.
DECISIONS_LOG_DEFAULT = ROOT.parent / "hitl-react" / "api" / "decisions.jsonl"


def resolutions_path(profile: str) -> Path:
    return DATA_SYNTHETIC / f"resolutions_{profile}.json"


# ── Stream replay (longitudinal eval — eval/replay.py) ───────────────────────
# The replay harness treats the drive as it looked each week: synthetic/
# generate_stream.py writes cumulative snapshots stream_<profile>/tick_NN/
# plus a per-tick ground truth, and eval/replay.py replays them through the
# UNCHANGED pipeline core, logging metrics per tick. Evaluation-side only —
# nothing in the pipeline core reads these.
STREAM_TICKS     = 9   # weekly snapshots; week 9 lets late-arriving patterns land
STREAM_TICK_DAYS = 7


def stream_dir(profile: str) -> Path:
    return DATA_SYNTHETIC / f"stream_{profile}"


def stream_gt_path(profile: str) -> Path:
    return DATA_SYNTHETIC / f"ground_truth_stream_{profile}.json"


def replay_log_path(profile: str) -> Path:
    return OUTPUTS / f"replay_{profile}.jsonl"


# ── Live SME simulator (simulator/) ──────────────────────────────────────────
# The simulator writes a drive that the product ingests exactly like any other
# messy drive: ingest.py --source messy --profile <p> --drive <sim drive>.
# Nothing in the pipeline core reads anything else under DATA_SIM.
DATA_SIM = ROOT / "data" / "sim"


def sim_dir(profile: str) -> Path:
    return DATA_SIM / profile


def sim_drive_dir(profile: str) -> Path:
    return sim_dir(profile) / "drive"


def sim_state_path(profile: str) -> Path:
    return sim_dir(profile) / "state.json"


def sim_inbox_path(profile: str) -> Path:
    return sim_dir(profile) / "inbox.jsonl"


def sim_cache_dir(profile: str) -> Path:
    return sim_dir(profile) / "cache"
