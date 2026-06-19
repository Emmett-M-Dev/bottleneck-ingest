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
