# Live SME Simulator — P1 (core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a headless world simulator that advances an SME's operational reality one day at a time, renders it to messy spreadsheets, and lets approved ActionItems change what happens next.

**Architecture:** A new `simulator/` package holds a mutable `WorldState` of cases that own append-only event lists. `advance()` runs five phases per sim day — arrivals, inbound messages, worker application, drift, render — and writes the drive atomically. The product is untouched: it ingests the rendered drive through the existing `ingest.py --drive` flag. Approved ActionItems are read from the product's action store and mapped to worker effects through a curated, config-driven table.

**Tech Stack:** Python 3, pandas, openpyxl, anthropic, pytest. No new dependencies.

## Global Constraints

- **The simulator must NEVER import `chromadb`, `pyarrow`, or `torch`**, directly or transitively. Importing pyarrow eagerly loads the Arrow C++ runtime, which segfaults in-process alongside chroma/hnswlib + torch on Windows (CLAUDE.md §6). Permitted imports: stdlib, `pandas`, `openpyxl`, `anthropic`, `config`, `actions.models`, `actions.store`, and `synthetic.generate_messy_advisory`. `actions/` is deliberately free of those three libraries — keep it that way.
- **The simulator must never import `detection/`, `pipeline/`, `eval/`, or `bridge/`.** One-way dependency: the product does not know the simulator exists, and the simulator does not know how detection works. This is the CLAUDE.md §7 circularity guard.
- **Excel I/O uses `engine="openpyxl"`** on every read and write.
- **Windows:** invoke `.venv/Scripts/python.exe` explicitly; bare `python` hits the Store stub. Set `PYTHONIOENCODING=utf-8` for any command whose output can contain `✔` (status values do).
- **No SME vocabulary in engine code.** Stage names, personas, intents, owners, probabilities and sheet names all come from `simulator/profiles.py`. `simulator/world.py`, `step.py`, `worker.py`, `compose.py` and `render.py` must contain no string literal naming an advisory stage, client or person.
- **Determinism:** every random draw comes from a `random.Random` seeded per `(world.seed, world.day)`. Never use the module-level `random` inside `simulator/` except in the one place the day-0 import requires it (Task 3, documented there).
- **Atomic writes:** every file the simulator writes goes through write-tmp-then-`os.replace`, matching `actions/store.py:44-50`.
- **Existing suite stays green.** `pytest -q` currently reports 202 passed. It must still pass after every task.
- Tag any step needing a human decision `[YOU]`; everything else is `[AGENT]`.

---

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `simulator/__init__.py` | package marker, nothing else |
| `simulator/profiles.py` | per-SME config block; the only file with advisory vocabulary |
| `simulator/world.py` | `SimEvent`, `SimCase`, `WorldState`, day-0 import, JSON round-trip |
| `simulator/render.py` | world → messy `.xlsx`, atomically |
| `simulator/intents.py` | generic inbound intent catalogue + selection |
| `simulator/compose.py` | message templates, LLM slot fill, on-disk cache, fallback |

**Deviation from the spec's module list:** the spec named a separate
`simulator/personas.py`. There is not enough in it to justify a module — persona
definitions are three lines of config (`profiles.ADVISORY["personas"]`) and the
sender name is one slot in `compose.py`. Splitting them would produce a file with
one constant in it. If persona behaviour grows past that (per-persona tone, thread
memory), extract it then.
| `simulator/worker.py` | applies messages and approved actions to the world |
| `simulator/step.py` | `advance()` — the five-phase day |
| `simulator/cli.py` | `--advance N` / `--reset` / `--status`, JSON to stdout |
| `tests/test_sim_world.py` … `tests/test_sim_e2e.py` | one test module per task |

**Modified:**

| File | Change |
|---|---|
| `detection/detect.py` | add `finding_key(bn)` helper |
| `bridge/export_cases.py:183` | emit `finding_key` alongside `case_id` |
| `actions/build.py:190,359` | key diagnosis by `finding_key`, fall back to `case_id` |
| `config.py` | add `DATA_SIM` and five `sim_*` path helpers |

---

### Task 1: Fix the `bn.id` diagnosis mis-attribution

This is the hazard CLAUDE.md §11 and HANDOVER §8 carry as known-and-unfixed. `detection/dynamic.py:176-178` sorts findings by impact and *then* labels them `BN001..N` by position. `bridge/export_cases.py:183` writes that positional id into `ui_cases_<profile>.json`, and `actions/build.py:359` builds `diagnosis_by_id` from it, then `actions/build.py:190` looks up `diagnosis_by_id[bn.id]` from an **independent** re-run of detection. Any change in ordering between the two runs silently attaches the wrong diagnosis prose to a finding. The simulator reorders findings every tick by design, so this goes from latent to guaranteed.

The fix uses the content-based key `actions/build.py:189` already computes for `finding_key`.

**Files:**
- Modify: `detection/detect.py` (add helper at end of file)
- Modify: `bridge/export_cases.py:180-190`
- Modify: `actions/build.py:188-191`, `actions/build.py:356-360`
- Test: `tests/test_finding_key.py`

**Interfaces:**
- Produces: `detection.detect.finding_key(bn: DetectedBottleneck) -> str`, returning `f"{bn.type}::{stage.strip().lower()}::{bn.metric_label}"`.
- Produces: `ui_cases_<profile>.json` case dicts gain a `"finding_key"` string field.

- [ ] **Step 1: Write the failing test**

Create `tests/test_finding_key.py`:

```python
from detection.detect import DetectedBottleneck, finding_key


def _bn(**kw):
    base = dict(id="BN001", type="delay", stage="Proposal",
                affected_cases=["NA-1", "NA-2"], metric_label="avg_delay_days",
                metric_value=18.0, example_refs=[])
    base.update(kw)
    return DetectedBottleneck(**base)


def test_finding_key_is_content_based_not_positional():
    a = _bn(id="BN001")
    b = _bn(id="BN007")
    assert finding_key(a) == finding_key(b)


def test_finding_key_canonicalises_stage_case_and_whitespace():
    assert finding_key(_bn(stage="Proposal")) == finding_key(_bn(stage=" PROPOSAL "))


def test_finding_key_separates_different_findings():
    assert finding_key(_bn(type="delay")) != finding_key(_bn(type="rework"))
    assert finding_key(_bn(stage="Proposal")) != finding_key(_bn(stage="Delivery"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_finding_key.py -v`
Expected: FAIL with `ImportError: cannot import name 'finding_key' from 'detection.detect'`

- [ ] **Step 3: Add the helper**

Append to `detection/detect.py`:

```python
def finding_key(bn: "DetectedBottleneck") -> str:
    """Content-based identity for a finding.

    `DetectedBottleneck.id` (BN001..N) is assigned by RANK ORDER in
    detect_dynamic, so it is not stable across two analyses of different data.
    Anything that joins across analyses — diagnosis prose, snapshots, action
    items — must key on this instead. See actions/build.py.
    """
    return f"{bn.type}::{str(bn.stage).strip().lower()}::{bn.metric_label}"
```

Note for later work: `detection/case_rules.py:394-395` assigns `CaseFinding.id`
the same way — `CF001..N` by rank order. `actions/build.py::_case_rule_items`
builds its items straight from the finding object and never joins on that id, so
there is no bug there today. It must stay that way: **neither `BN` nor `CF` ids
may ever be used as a join key across two analyses.**

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_finding_key.py -v`
Expected: 3 passed

- [ ] **Step 5: Emit the key from the exporter**

In `bridge/export_cases.py`, add the import alongside the existing `DetectedBottleneck` import:

```python
from detection.detect import DetectedBottleneck, finding_key
```

Then in `build_cases()`, in the dict literal that currently starts `"case_id": bn.id,` (line 183), add one line immediately after it:

```python
            "case_id": bn.id,
            "finding_key": finding_key(bn),
```

- [ ] **Step 6: Consume the key in the action builder**

In `actions/build.py`, change the map construction (line 359) to prefer the content key and keep the positional one as a fallback for exports written before this change:

```python
    diagnosis_by_id = {}
    for c in cases:
        if c.get("type") == "anomaly":
            continue
        if c.get("case_id"):
            diagnosis_by_id[c["case_id"]] = c        # legacy exports
        if c.get("finding_key"):
            diagnosis_by_id[c["finding_key"]] = c    # content key wins
```

And change the lookup (line 190) to use the content key, falling back to the
positional id **only when the export is wholly legacy** — that is, when no entry
in `cases` carries a `finding_key` at all:

```python
        diag = diagnosis_by_id.get(key) or {}
        if not diag and legacy_export:
            diag = diagnosis_by_id.get(bn.id) or {}
```

where `legacy_export` is computed once, alongside the map:

```python
    legacy_export = not any(c.get("finding_key") for c in cases)
```

`key` is already computed on the line above as the content-based finding key, and
is identical in form to what `finding_key()` returns.

> **Correction, 2026-08-02.** This step originally applied the `bn.id` fallback
> per item. Review caught that a *current* export (one carrying `finding_key`)
> would still fall through to a rank-order id whenever a finding's content key
> was absent — re-introducing, in narrowed form, the exact mis-attribution this
> task exists to remove, and contradicting the Global Constraint that BN/CF ids
> are never join keys. Ruled in the reviewer's favour; the code above is the
> corrected version.

- [ ] **Step 7: Write the regression test for the join**

> **Known defect in the sketch below, 2026-08-02.** The `_events()` helper builds
> no `stage` column and keeps `ts` as strings, and the three-case dataset gives
> every case an identical 22-day gap — so `detect_dynamic`'s outlier threshold
> (Q3 + 1.5·IQR over all gaps) exceeds every gap present and **no delay finding
> fires at all**. Build a dataset with a background of small-gap cases plus a few
> genuine outliers, and pass `ts` as datetimes. The assertion structure below is
> correct; only the data is not.

Append to `tests/test_finding_key.py`:

```python
import pandas as pd

from actions.build import build_action_items


def _events(rows):
    return pd.DataFrame(rows, columns=["case_id", "activity", "ts", "actor",
                                       "status", "source_ref", "value"])


def test_diagnosis_attaches_by_content_not_by_rank_order():
    """A ui_cases export whose BN ids are in a DIFFERENT order from this run's
    detection must still land its prose on the right finding."""
    rows = []
    for n in range(1, 4):                    # three cases, delay at Proposal
        cid = f"NA-{n}"
        rows += [
            (cid, "Lead", "2026-01-01", "R", "done", "x.xlsx:1", 1000),
            (cid, "Qualification", "2026-01-03", "R", "done", "x.xlsx:2", 1000),
            (cid, "Proposal", "2026-01-25", "R", "done", "x.xlsx:3", 1000),
        ]
    df = _events(rows)

    cases = [{
        "case_id": "BN099",                  # deliberately wrong positional id
        "finding_key": "delay::proposal::avg_delay_days",
        "type": "delay",
        "title": "CONTENT-MATCHED TITLE",
        "description": "matched by content key",
    }]

    items = build_action_items("advisory", df, cases=cases)
    delays = [i for i in items if i.finding_type == "delay"]
    assert delays, "expected a delay finding"
    assert delays[0].title == "CONTENT-MATCHED TITLE"
```

- [ ] **Step 8: Run the new test and the full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_finding_key.py -v`
Expected: 4 passed

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -q`
Expected: 206 passed (202 existing + 4 new). If any existing test fails, the fallback branch in Step 6 is wrong — do not delete the failing test.

- [ ] **Step 9: Commit**

```bash
git add detection/detect.py bridge/export_cases.py actions/build.py tests/test_finding_key.py
git commit -m "Key diagnosis prose to findings by content, not rank order"
```

---

### Task 2: Config paths and the advisory sim profile

**Files:**
- Modify: `config.py` (append after the stream-replay block, around line 344)
- Create: `simulator/__init__.py`, `simulator/profiles.py`
- Test: `tests/test_sim_profiles.py`

**Interfaces:**
- Produces: `config.DATA_SIM`, `config.sim_dir(profile)`, `config.sim_drive_dir(profile)`, `config.sim_state_path(profile)`, `config.sim_inbox_path(profile)`, `config.sim_cache_dir(profile)` — all return `Path`.
- Produces: `simulator.profiles.SIM_PROFILES: dict[str, dict]` and `simulator.profiles.profile_config(name: str) -> dict` (raises `KeyError` with the list of known profiles on a miss).

- [ ] **Step 1: Write the failing test**

Create `tests/test_sim_profiles.py`:

```python
import pytest

import config
from simulator.profiles import SIM_PROFILES, profile_config


def test_paths_are_under_data_sim():
    assert config.sim_dir("advisory").name == "advisory"
    assert config.sim_dir("advisory").parent == config.DATA_SIM
    assert config.sim_drive_dir("advisory").name == "drive"
    assert config.sim_state_path("advisory").name == "state.json"
    assert config.sim_inbox_path("advisory").name == "inbox.jsonl"


def test_advisory_profile_is_wired():
    cfg = profile_config("advisory")
    for field in ("generator", "stage_order", "first_stage", "terminal_stages",
                  "arrival_rate_per_day", "params", "effect_prob",
                  "process_param_delta", "personas", "intents"):
        assert field in cfg, f"missing {field}"


def test_every_wired_finding_type_has_a_probability():
    wired = {"stage_sla_breach", "stalled_case", "unowned_case",
             "unrealised_value", "overloaded_owner", "key_person_dependency"}
    assert set(profile_config("advisory")["effect_prob"]) == wired


def test_unknown_profile_names_the_known_ones():
    with pytest.raises(KeyError, match="advisory"):
        profile_config("nope")


def test_only_advisory_is_wired_for_now():
    assert set(SIM_PROFILES) == {"advisory"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_profiles.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simulator'`

- [ ] **Step 3: Add the config paths**

Append to `config.py`:

```python
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
```

- [ ] **Step 4: Create the package and profile block**

Create `simulator/__init__.py` containing only:

```python
"""Live SME simulator — the data generator (System 2).

Plays both sides of the SME's correspondence: the clients who send work in and
the staff who type it into spreadsheets. Writes a messy drive the product
ingests through its existing --drive flag; never imports the product.
"""
```

Create `simulator/profiles.py`:

```python
"""Per-SME simulator configuration.

THE ONLY FILE IN simulator/ ALLOWED TO NAME AN SME'S STAGES, PEOPLE OR CLIENTS.
Onboarding SME #2 means adding a block here and writing no engine code — the
same claim CLAUDE.md §3 makes for the pipeline core.
"""

from __future__ import annotations

import config

ADVISORY: dict = {
    # Module supplying day 0 and the sheet writers. Imported lazily so this
    # file stays cheap.
    "generator": "synthetic.generate_messy_advisory",
    "stage_order": config.MESSY_PROFILES["advisory"]["stage_order"],
    "first_stage": "Lead",
    "terminal_stages": ["Paid"],
    "case_id_fmt": "NA-{:04d}",

    # New enquiries per sim day, Poisson mean. Roughly three a week.
    "arrival_rate_per_day": 0.45,

    # Mutable simulator parameters. Approved process interventions move these;
    # nothing else does. Probability that a case sitting at a stage FAILS to
    # move on any given day.
    "params": {
        "stall_prob.Lead": 0.55,
        "stall_prob.Qualification": 0.45,
        "stall_prob.Proposal": 0.70,
        "stall_prob.Won": 0.40,
        "stall_prob.Onboarding": 0.45,
        "stall_prob.Delivery": 0.60,
        "stall_prob.Client Review": 0.65,
        "stall_prob.Invoice": 0.55,
        "repetition_prob": 0.06,
        "rework_prob": 0.05,
    },

    # Probability that an APPROVED action of this finding type actually works.
    # Deliberately below 1.0: a simulator in which every approved fix succeeds
    # is authored to flatter the product.
    "effect_prob": {
        "stage_sla_breach": 0.70,
        "stalled_case": 0.60,
        "unowned_case": 0.95,
        "unrealised_value": 0.50,
        "overloaded_owner": 0.80,
        "key_person_dependency": 0.40,
    },

    # Approved process interventions on a structural finding shift a parameter
    # for cases from that tick FORWARD. A process fix does not repair history.
    "process_param_delta": {
        "delay": {"stall_prob.Proposal": -0.20},
        "repetition": {"repetition_prob": -0.04},
        "rework": {"rework_prob": -0.03},
    },
    "param_floor": 0.02,

    "personas": [
        {"id": "client", "label": "client contact"},
        {"id": "prospect", "label": "new enquiry"},
        {"id": "supplier", "label": "subcontractor"},
    ],

    # Which intents can fire, and their relative weights.
    "intents": {
        "new_enquiry": 3,
        "progress_update": 6,
        "client_query": 4,
        "payment_made": 2,
        "scope_change": 1,
    },
}

SIM_PROFILES: dict[str, dict] = {"advisory": ADVISORY}


def profile_config(name: str) -> dict:
    if name not in SIM_PROFILES:
        raise KeyError(f"unknown sim profile {name!r}; "
                       f"known: {sorted(SIM_PROFILES)}")
    return SIM_PROFILES[name]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_profiles.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add config.py simulator/__init__.py simulator/profiles.py tests/test_sim_profiles.py
git commit -m "Add simulator package skeleton, sim paths and the advisory profile block"
```

---

### Task 3: World state and the day-0 import

**Files:**
- Create: `simulator/world.py`
- Test: `tests/test_sim_world.py`

**Interfaces:**
- Produces: `SimEvent(stage: str, ts: datetime, actor: str, status: str)` — dataclass. `stage` is **canonical** (matches `stage_order` exactly); the messy surface form is applied at render time.
- Produces: `SimCase` with fields `cid: str`, `client: str`, `value: float`, `events: list[SimEvent]`, and properties `last_ts -> datetime`, `stage -> str` (canonical stage of the last event), `owner -> str` (actor of the last event, `""` if unowned).

  **Correction, 2026-08-02.** An earlier draft of this plan claimed the field names duck-type `synthetic.generate_messy_advisory.Engagement` closely enough that the generator's reference-sheet builders could be reused *unchanged*. That is false and review proved it: `Engagement.events` is `list[dict]` accessed by key (`e["activity"]`, `e["ts"]`, …), while `SimCase.events` is `list[SimEvent]` with a canonical `stage` field. `build_clients` works (it touches only case-level fields); `build_timesheets` and `build_invoices` raise `TypeError: 'SimEvent' object is not subscriptable`. The duck-typing holds **at the case level only**. Task 4's `_EngagementLike` adapter is what supplies the dict shape, which is also where the messy stage spelling is re-applied — see Task 4.
- Produces: `WorldState` with fields `profile`, `seed`, `day`, `start_date: datetime`, `cases: dict[str, SimCase]`, `params: dict[str, float]`, `next_case_num: int`, `intent: dict`; property `current_date -> datetime`; methods `to_dict()`, `rng_for_day(day) -> random.Random`.
- Produces: `from_dict(d: dict) -> WorldState`, `day0_from_generator(profile: str) -> WorldState`, `canon_stage(label: str, stage_order: list[str]) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sim_world.py`:

```python
from datetime import datetime

from simulator.world import (SimCase, SimEvent, WorldState, canon_stage,
                             day0_from_generator, from_dict)


def _case():
    return SimCase(cid="NA-1", client="Rowan Group", value=1000.0, events=[
        SimEvent(stage="Lead", ts=datetime(2026, 1, 1), actor="R", status="done"),
        SimEvent(stage="Proposal", ts=datetime(2026, 1, 9), actor="", status="tbc"),
    ])


def test_canon_stage_matches_ignoring_case_and_whitespace():
    order = ["Lead", "Client Review", "Paid"]
    assert canon_stage("  client review ", order) == "Client Review"
    assert canon_stage("LEAD", order) == "Lead"
    assert canon_stage("Mystery", order) == "Mystery"     # unknown passes through


def test_case_exposes_stage_owner_and_last_ts():
    c = _case()
    assert c.stage == "Proposal"
    assert c.owner == ""
    assert c.last_ts == datetime(2026, 1, 9)


def test_world_round_trips_through_dict():
    w = WorldState(profile="advisory", seed=7, day=3,
                   start_date=datetime(2026, 7, 20), cases={"NA-1": _case()},
                   params={"stall_prob.Lead": 0.5}, next_case_num=1042,
                   intent={"arrivals": {}})
    back = from_dict(w.to_dict())
    assert back.day == 3
    assert back.current_date == datetime(2026, 7, 23)
    assert back.cases["NA-1"].stage == "Proposal"
    assert back.params == {"stall_prob.Lead": 0.5}


def test_rng_is_deterministic_per_day_and_differs_across_days():
    w = WorldState(profile="advisory", seed=7, day=0,
                   start_date=datetime(2026, 7, 20), cases={}, params={},
                   next_case_num=1, intent={})
    assert w.rng_for_day(4).random() == w.rng_for_day(4).random()
    assert w.rng_for_day(4).random() != w.rng_for_day(5).random()


def test_day0_import_loads_the_advisory_world():
    w = day0_from_generator("advisory")
    assert w.day == 0
    assert len(w.cases) == 27              # len(_PLAN) in the generator
    assert w.next_case_num == 1041 + 27    # ids run NA-1041 .. NA-1067
    # every event stage canonicalised against the profile's stage order
    order = set(w.intent["stage_order"])
    stages = {e.stage for c in w.cases.values() for e in c.events}
    assert stages <= order, f"uncanonicalised stages: {stages - order}"
    # the generator's own ground truth is carried through as recorded INTENT
    assert w.intent["structural"]["delay"]
    assert w.intent["operational"]["parked_at"]


def test_day0_import_is_reproducible():
    a, b = day0_from_generator("advisory"), day0_from_generator("advisory")
    assert a.to_dict() == b.to_dict()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_world.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simulator.world'`

- [ ] **Step 3: Write the implementation**

Create `simulator/world.py`:

```python
"""The world the simulator advances.

A WorldState is a bag of cases; a case owns an append-only list of events.
Stages are stored CANONICAL here — the messy surface spelling that staff
actually type is applied by simulator/render.py, so world logic never has to
parse mess it created itself.
"""

from __future__ import annotations

import importlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from simulator.profiles import profile_config

_ISO = "%Y-%m-%dT%H:%M:%S"


def canon_stage(label: str, stage_order: list[str]) -> str:
    """Map a possibly-messy stage label onto the profile's canonical spelling.
    Unknown labels pass through unchanged rather than being dropped."""
    lookup = {s.strip().lower(): s for s in stage_order}
    return lookup.get(str(label).strip().lower(), str(label).strip())


@dataclass
class SimEvent:
    stage: str
    ts: datetime
    actor: str
    status: str

    def to_dict(self) -> dict:
        return {"stage": self.stage, "ts": self.ts.strftime(_ISO),
                "actor": self.actor, "status": self.status}

    @staticmethod
    def from_dict(d: dict) -> "SimEvent":
        return SimEvent(stage=d["stage"], ts=datetime.strptime(d["ts"], _ISO),
                        actor=d["actor"], status=d["status"])


@dataclass
class SimCase:
    """One case, owning an append-only event list.

    Case-level field names (cid/client/value) mirror
    generate_messy_advisory.Engagement, so a SimCase can stand in where an
    Engagement is expected. `events` does NOT: it holds SimEvent dataclasses
    with a CANONICAL `stage`, where the generator holds dicts with a messy
    `activity`. Any generator builder that subscripts events needs the adapter
    in simulator/render.py, which is also where messy spelling is re-applied.
    """
    cid: str
    client: str
    value: float
    events: list[SimEvent] = field(default_factory=list)

    @property
    def last_ts(self) -> datetime:
        return self.events[-1].ts

    @property
    def stage(self) -> str:
        return self.events[-1].stage

    @property
    def owner(self) -> str:
        return self.events[-1].actor or ""

    def add(self, stage: str, ts: datetime, actor: str, status: str) -> None:
        self.events.append(SimEvent(stage=stage, ts=ts, actor=actor,
                                    status=status))

    def to_dict(self) -> dict:
        return {"cid": self.cid, "client": self.client, "value": self.value,
                "events": [e.to_dict() for e in self.events]}

    @staticmethod
    def from_dict(d: dict) -> "SimCase":
        return SimCase(cid=d["cid"], client=d["client"], value=d["value"],
                       events=[SimEvent.from_dict(e) for e in d["events"]])


@dataclass
class WorldState:
    profile: str
    seed: int
    day: int
    start_date: datetime
    cases: dict[str, SimCase]
    params: dict[str, float]
    next_case_num: int
    intent: dict

    @property
    def current_date(self) -> datetime:
        return self.start_date + timedelta(days=self.day)

    def rng_for_day(self, day: int) -> random.Random:
        """One generator per (seed, day). Same seed + same approvals => the
        same world, which is what makes the eval replay reproducible."""
        return random.Random(f"{self.seed}|{day}")

    def to_dict(self) -> dict:
        return {
            "profile": self.profile, "seed": self.seed, "day": self.day,
            "start_date": self.start_date.strftime(_ISO),
            "cases": {k: v.to_dict() for k, v in self.cases.items()},
            "params": self.params, "next_case_num": self.next_case_num,
            "intent": self.intent,
        }


def from_dict(d: dict) -> WorldState:
    return WorldState(
        profile=d["profile"], seed=d["seed"], day=d["day"],
        start_date=datetime.strptime(d["start_date"], _ISO),
        cases={k: SimCase.from_dict(v) for k, v in d["cases"].items()},
        params=dict(d["params"]), next_case_num=d["next_case_num"],
        intent=d["intent"],
    )


def day0_from_generator(profile: str) -> WorldState:
    """Import the existing hand-authored world as day 0.

    The generator uses the MODULE-LEVEL random, so it is seeded here before
    build_events() is called. This is the one place in simulator/ that touches
    module-level random; everything downstream uses WorldState.rng_for_day.
    """
    cfg = profile_config(profile)
    mod = importlib.import_module(cfg["generator"])
    random.seed(mod.SEED)
    engagements, structural, operational = mod.build_events()

    order = cfg["stage_order"]
    cases: dict[str, SimCase] = {}
    for eng in engagements:
        cases[eng.cid] = SimCase(
            cid=eng.cid, client=eng.client, value=float(eng.value),
            events=[SimEvent(stage=canon_stage(e["activity"], order),
                             ts=e["ts"], actor=e["actor"] or "",
                             status=e["status"]) for e in eng.events])

    return WorldState(
        profile=profile, seed=mod.SEED, day=0, start_date=mod.AS_OF,
        cases=cases, params=dict(cfg["params"]),
        next_case_num=1041 + len(engagements),
        intent={"stage_order": list(order), "structural": structural,
                "operational": operational, "arrivals": {}, "effects": []},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_world.py -v`
Expected: 6 passed.
If `test_day0_import_loads_the_advisory_world` fails on the case count, read `_PLAN` in `synthetic/generate_messy_advisory.py:211-247` and use its actual length — do not change the generator.

- [ ] **Step 5: Commit**

```bash
git add simulator/world.py tests/test_sim_world.py
git commit -m "Add simulator world state and day-0 import from the advisory generator"
```

---

### Task 4: Atomic render — world to messy spreadsheets

**Files:**
- Create: `simulator/render.py`
- Test: `tests/test_sim_render.py`

**Interfaces:**
- Consumes: `simulator.world.WorldState`, `simulator.profiles.profile_config`.
- Produces: `render(world: WorldState, out_dir: Path, rng: random.Random) -> list[str]` returning the filenames written, and `event_dicts(world, mod, rng) -> tuple[list[dict], list[dict]]` returning `(sales_events, delivery_events)` in the shape the generator's frame builders expect.

The renderer reuses `generate_messy_advisory._lead_frame`, `._project_frame`, `._mess`, `.build_clients`, `.build_timesheets`, `.build_team_capacity` and `.build_invoices` so the *mess* — inconsistent stage spellings, the renamed-header fork, the duplicated "final" copy — is defined in exactly one place.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sim_render.py`:

```python
import random

import pandas as pd

from simulator.render import render
from simulator.world import day0_from_generator


def test_render_writes_the_expected_file_set(tmp_path):
    w = day0_from_generator("advisory")
    names = render(w, tmp_path, random.Random(1))
    assert set(names) == {
        "leads.xlsx", "projects.xlsx", "projects - final NEW.xlsx",
        "clients.xlsx", "timesheets.xlsx", "team_capacity.xlsx",
        "invoices.xlsx"}
    for n in names:
        assert (tmp_path / n).exists()


def test_render_leaves_no_temp_files(tmp_path):
    render(day0_from_generator("advisory"), tmp_path, random.Random(1))
    assert not list(tmp_path.glob("*.tmp"))


def test_rendered_stages_are_messy_not_canonical(tmp_path):
    """The mess is the point: staff type stage names inconsistently and the
    canonicalisation upstream has to survive it."""
    render(day0_from_generator("advisory"), tmp_path, random.Random(1))
    df = pd.read_excel(tmp_path / "leads.xlsx", engine="openpyxl")
    stages = set(df["Stage"].astype(str))
    assert len(stages) > len({s.strip().title() for s in stages}), \
        "expected inconsistent spellings of the same stage"


def test_render_is_deterministic_for_a_given_rng_seed(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    w = day0_from_generator("advisory")
    render(w, a, random.Random(99))
    render(w, b, random.Random(99))
    for n in ("leads.xlsx", "projects.xlsx"):
        left = pd.read_excel(a / n, engine="openpyxl")
        right = pd.read_excel(b / n, engine="openpyxl")
        pd.testing.assert_frame_equal(left, right)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simulator.render'`

- [ ] **Step 3: Write the implementation**

Create `simulator/render.py`:

```python
"""Project the world onto the SME's messy spreadsheets.

Every write is tmp-then-os.replace, so the product can be mid-ingest while a
tick renders. Mess (stage spelling, the renamed-header fork, the duplicated
'final' copy) is defined once, in the generator, and reused here.
"""

from __future__ import annotations

import importlib
import os
import random
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from simulator.profiles import profile_config
from simulator.world import WorldState


@dataclass
class _EngagementLike:
    """What the generator's reference-sheet builders expect to be handed."""
    cid: str
    client: str
    value: float
    events: list[dict]

    @property
    def last_ts(self) -> datetime:
        return self.events[-1]["ts"]


def _write_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(fd)
    try:
        df.to_excel(tmp, index=False, engine="openpyxl")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _engagement_likes(world: WorldState, mod, rng: random.Random
                      ) -> list[_EngagementLike]:
    out = []
    for case in world.cases.values():
        rows = [{"case_id": case.cid, "activity": _mess(mod, e.stage, rng),
                 "ts": e.ts, "actor": e.actor, "status": e.status,
                 "value": case.value, "client": case.client}
                for e in case.events]
        out.append(_EngagementLike(cid=case.cid, client=case.client,
                                   value=case.value, events=rows))
    return out


def _mess(mod, stage: str, rng: random.Random) -> str:
    """The generator's _mess uses module-level random; reproduce its variants
    against our own rng so a tick stays deterministic."""
    return rng.choice([stage, stage.upper(), stage.lower(), stage + " "])


def event_dicts(world: WorldState, mod, rng: random.Random
                ) -> tuple[list[dict], list[dict]]:
    sales_stages = {s.lower() for s in mod.SALES_STAGES}
    sales, delivery = [], []
    for eng in _engagement_likes(world, mod, rng):
        for row in eng.events:
            target = (sales if row["activity"].strip().lower() in sales_stages
                      else delivery)
            target.append(row)
    sales.sort(key=lambda r: (r["ts"], r["case_id"]))
    delivery.sort(key=lambda r: (r["ts"], r["case_id"]))
    return sales, delivery


def render(world: WorldState, out_dir: Path, rng: random.Random) -> list[str]:
    cfg = profile_config(world.profile)
    mod = importlib.import_module(cfg["generator"])
    out_dir = Path(out_dir)

    engagements = _engagement_likes(world, mod, rng)
    sales, delivery = event_dicts(world, mod, rng)

    # Somebody saved a 'final' copy of the delivery sheet: older rows duplicated
    # verbatim, a few newer ones only there. Same rule as the static drive.
    fork_rows = delivery[:14] + delivery[-6:]

    writes = [
        ("leads.xlsx", mod._lead_frame(sales)),
        ("projects.xlsx", mod._project_frame(delivery)),
        ("projects - final NEW.xlsx", mod._project_frame(fork_rows)),
        ("clients.xlsx", mod.build_clients(engagements)),
        ("timesheets.xlsx", mod.build_timesheets(engagements)),
        ("team_capacity.xlsx", mod.build_team_capacity()),
        ("invoices.xlsx", mod.build_invoices(engagements)),
    ]
    for name, df in writes:
        _write_atomic(df, out_dir / name)
    return [name for name, _ in writes]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_render.py -v`
Expected: 4 passed.
If `build_timesheets` or `build_invoices` raises `AttributeError`, read its signature in `synthetic/generate_messy_advisory.py` and add the missing attribute to `_EngagementLike` — do not modify the generator.

- [ ] **Step 5: Commit**

```bash
git add simulator/render.py tests/test_sim_render.py
git commit -m "Render simulator world state to the messy drive atomically"
```

---

### Task 5: Intent catalogue

**Files:**
- Create: `simulator/intents.py`
- Test: `tests/test_sim_intents.py`

**Interfaces:**
- Produces: `Intent` — a frozen dataclass with `id: str`, `needs_case: bool`, `persona: str`.
- Produces: `CATALOGUE: dict[str, Intent]` keyed by intent id, covering `new_enquiry`, `progress_update`, `client_query`, `payment_made`, `scope_change`.
- Produces: `choose(world, rng, cfg) -> list[tuple[Intent, str | None]]` returning `(intent, case_id)` pairs for one day; `case_id` is `None` only for `new_enquiry`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sim_intents.py`:

```python
from simulator.intents import CATALOGUE, choose
from simulator.profiles import profile_config
from simulator.world import day0_from_generator


def test_catalogue_covers_the_configured_intents():
    assert set(CATALOGUE) >= set(profile_config("advisory")["intents"])


def test_only_new_enquiry_may_have_no_case():
    for intent in CATALOGUE.values():
        assert intent.needs_case == (intent.id != "new_enquiry")


def test_choose_is_deterministic_for_a_seed():
    w = day0_from_generator("advisory")
    cfg = profile_config("advisory")
    a = choose(w, w.rng_for_day(1), cfg)
    b = choose(w, w.rng_for_day(1), cfg)
    assert [(i.id, c) for i, c in a] == [(i.id, c) for i, c in b]


def test_case_bound_intents_target_a_live_case():
    w = day0_from_generator("advisory")
    cfg = profile_config("advisory")
    terminal = {s.lower() for s in cfg["terminal_stages"]}
    for intent, cid in choose(w, w.rng_for_day(3), cfg):
        if intent.needs_case:
            assert cid in w.cases
            assert w.cases[cid].stage.lower() not in terminal
        else:
            assert cid is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_intents.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simulator.intents'`

- [ ] **Step 3: Write the implementation**

Create `simulator/intents.py`:

```python
"""What arrives in the SME's inbox on a given day.

Generic: an intent knows whether it needs a live case and who tends to send it,
never which SME it belongs to. Weights and personas come from the profile.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from simulator.world import WorldState


@dataclass(frozen=True)
class Intent:
    id: str
    needs_case: bool
    persona: str


CATALOGUE: dict[str, Intent] = {
    "new_enquiry":     Intent("new_enquiry", False, "prospect"),
    "progress_update": Intent("progress_update", True, "client"),
    "client_query":    Intent("client_query", True, "client"),
    "payment_made":    Intent("payment_made", True, "client"),
    "scope_change":    Intent("scope_change", True, "client"),
}


def _live_case_ids(world: WorldState, cfg: dict) -> list[str]:
    terminal = {s.strip().lower() for s in cfg["terminal_stages"]}
    return sorted(cid for cid, c in world.cases.items()
                  if c.stage.strip().lower() not in terminal)


def _poisson(rng: random.Random, mean: float) -> int:
    """Knuth's method — small means only, which is all an SME day needs."""
    import math
    limit, k, p = math.exp(-mean), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def choose(world: WorldState, rng: random.Random, cfg: dict
           ) -> list[tuple[Intent, str | None]]:
    """The day's inbound traffic: (intent, case_id) pairs."""
    live = _live_case_ids(world, cfg)
    weights = cfg["intents"]
    ids = sorted(weights)

    out: list[tuple[Intent, str | None]] = []

    for _ in range(_poisson(rng, cfg["arrival_rate_per_day"])):
        out.append((CATALOGUE["new_enquiry"], None))

    # Roughly one touch per twelve live cases per day, floor of zero.
    for _ in range(_poisson(rng, len(live) / 12.0)):
        if not live:
            break
        pool = [i for i in ids if CATALOGUE[i].needs_case]
        pick = rng.choices(pool, weights=[weights[i] for i in pool], k=1)[0]
        out.append((CATALOGUE[pick], rng.choice(live)))

    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_intents.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add simulator/intents.py tests/test_sim_intents.py
git commit -m "Add the generic inbound intent catalogue"
```

---

### Task 6: Message composition — templates, LLM slot fill, cache

**Files:**
- Create: `simulator/compose.py`
- Test: `tests/test_sim_compose.py`

**Interfaces:**
- Consumes: `simulator.intents.Intent`.
- Produces: `Message` dataclass — `msg_id`, `day`, `sent_at`, `sender`, `subject`, `body`, `intent`, `case_id`, `applied: bool`, `row_ref: str | None`, plus `to_dict()`.
- Produces: `compose(intent, *, day, seq, case, rng, cache_dir, use_llm) -> Message`.
- Produces: `TEMPLATES: dict[str, dict[str, str]]` — per intent, a `subject` and `body` with `{slot}` placeholders.

The LLM fills **slot values only** — names, counts, figures, a phrasing variant. It never decides which case, which stage, or whether anything breached. Fills are cached at `cache_dir/<seed>-<day>-<msg_id>.json`; a cache hit makes a replay free and offline. With no key, or on any API error, deterministic fallback values from `rng` are used and the message still sends.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sim_compose.py`:

```python
import json
import random

from simulator.compose import TEMPLATES, compose
from simulator.intents import CATALOGUE
from simulator.world import day0_from_generator


def _case():
    return next(iter(day0_from_generator("advisory").cases.values()))


def test_every_catalogue_intent_has_a_template():
    assert set(TEMPLATES) == set(CATALOGUE)


def test_compose_without_llm_is_deterministic(tmp_path):
    kw = dict(day=3, seq=1, case=_case(), cache_dir=tmp_path, use_llm=False)
    a = compose(CATALOGUE["client_query"], rng=random.Random(5), **kw)
    b = compose(CATALOGUE["client_query"], rng=random.Random(5), **kw)
    assert (a.subject, a.body) == (b.subject, b.body)


def test_compose_leaves_no_unfilled_slots(tmp_path):
    for intent in CATALOGUE.values():
        m = compose(intent, day=1, seq=0, case=_case(), rng=random.Random(1),
                    cache_dir=tmp_path, use_llm=False)
        assert "{" not in m.subject and "{" not in m.body, intent.id


def test_compose_starts_unapplied_with_a_stable_id(tmp_path):
    m = compose(CATALOGUE["payment_made"], day=12, seq=2, case=_case(),
                rng=random.Random(1), cache_dir=tmp_path, use_llm=False)
    assert m.msg_id == "M012-02"
    assert m.applied is False and m.row_ref is None


def test_cached_slots_are_reused_instead_of_regenerated(tmp_path):
    case = _case()
    (tmp_path / "0-4-M004-00.json").write_text(
        json.dumps({"detail": "CACHED DETAIL"}), encoding="utf-8")
    m = compose(CATALOGUE["client_query"], day=4, seq=0, case=case,
                rng=random.Random(1), cache_dir=tmp_path, use_llm=True,
                seed=0)
    assert "CACHED DETAIL" in m.body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_compose.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simulator.compose'`

- [ ] **Step 3: Write the implementation**

Create `simulator/compose.py`:

```python
"""Turn an intent into an email.

Template skeletons carry the structure; the LLM fills SLOT VALUES ONLY (names,
counts, figures, a phrasing variant). It never decides which case, which stage,
or whether anything breached — that stays deterministic so ground truth stays
computable and the circularity guard holds.

Fills are cached per (seed, day, msg_id), so a replayed run costs nothing and
runs offline. No key, or any API failure, falls back to deterministic values
and the message still sends.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

TEMPLATES: dict[str, dict[str, str]] = {
    "new_enquiry": {
        "subject": "Enquiry - {detail}",
        "body": ("Hi,\n\nWe're looking for support on {detail}. "
                 "Roughly {figure} to spend. Can you take this on?\n\n{sender}"),
    },
    "progress_update": {
        "subject": "Re: {case_id} - update",
        "body": ("Hi,\n\nJust confirming {detail} on {case_id}. "
                 "Happy for you to move it on.\n\n{sender}"),
    },
    "client_query": {
        "subject": "Question on {case_id}",
        "body": ("Hi,\n\nQuick one - {detail}. Can you come back to me?"
                 "\n\n{sender}"),
    },
    "payment_made": {
        "subject": "{case_id} - payment sent",
        "body": ("Hi,\n\nPayment of {figure} went out today for {case_id}. "
                 "{detail}\n\n{sender}"),
    },
    "scope_change": {
        "subject": "{case_id} - change of scope",
        "body": ("Hi,\n\nWe need to revisit part of this - {detail}. "
                 "Sorry for the go-around.\n\n{sender}"),
    },
}

_FALLBACK_DETAIL = [
    "the March intake", "the reporting pack", "next quarter's phasing",
    "the site visit dates", "the draft findings", "the onboarding pack",
]
_FALLBACK_SENDER = ["R. Hughes", "M. Doherty", "S. Cassidy", "P. Neill",
                    "A. Lynch", "T. Bradley"]


@dataclass
class Message:
    msg_id: str
    day: int
    sent_at: str
    sender: str
    subject: str
    body: str
    intent: str
    case_id: str | None
    applied: bool = False
    row_ref: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _fallback_slots(case, rng: random.Random) -> dict[str, str]:
    value = getattr(case, "value", 0) or 0
    return {
        "detail": rng.choice(_FALLBACK_DETAIL),
        "sender": rng.choice(_FALLBACK_SENDER),
        "figure": f"£{int(value):,}" if value else "£8,000",
    }


def _cache_path(cache_dir: Path, seed: int, day: int, msg_id: str) -> Path:
    return Path(cache_dir) / f"{seed}-{day}-{msg_id}.json"


def _llm_slots(intent_id: str, case, model: str) -> dict[str, str] | None:
    """Ask Claude for slot values. Returns None on any failure — the caller
    falls back, so a missing key or a flaky network degrades the prose, never
    the run."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        prompt = (
            "You are writing ONE short, plain business email from a client to "
            "a small consultancy. Reply with JSON only, keys exactly: "
            "detail, sender, figure. 'detail' is a short noun phrase (max 8 "
            "words) about the work. 'sender' is a plausible name. 'figure' is "
            f"a money amount with a pound sign. The email intent is "
            f"'{intent_id}'. Keep it basic and unremarkable."
        )
        resp = client.messages.create(
            model=model, max_tokens=200,
            messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in resp.content if b.type == "text")
        slots = json.loads(text[text.index("{"):text.rindex("}") + 1])
        return {k: str(slots[k]) for k in ("detail", "sender", "figure")}
    except Exception:
        return None


def compose(intent, *, day: int, seq: int, case, rng: random.Random,
            cache_dir, use_llm: bool, seed: int = 0,
            model: str | None = None, start_date=None) -> Message:
    import config

    msg_id = f"M{day:03d}-{seq:02d}"
    cache = _cache_path(cache_dir, seed, day, msg_id)

    slots = _fallback_slots(case, rng)
    if cache.exists():
        slots.update(json.loads(cache.read_text(encoding="utf-8")))
    elif use_llm:
        got = _llm_slots(intent.id, case,
                         model or config.DIAGNOSE_MODEL_DEFAULT)
        if got:
            slots.update(got)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(got), encoding="utf-8")

    slots["case_id"] = getattr(case, "cid", "") if case else ""
    tpl = TEMPLATES[intent.id]
    sent = (start_date + timedelta(days=day)) if start_date else None

    return Message(
        msg_id=msg_id, day=day,
        sent_at=sent.isoformat() if sent else "",
        sender=slots["sender"],
        subject=tpl["subject"].format(**slots),
        body=tpl["body"].format(**slots),
        intent=intent.id,
        case_id=getattr(case, "cid", None) if case else None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_compose.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add simulator/compose.py tests/test_sim_compose.py
git commit -m "Compose simulator messages from templates with cached LLM slot fill"
```

---

### Task 7: The worker — messages and approved actions become row changes

**Files:**
- Create: `simulator/worker.py`
- Test: `tests/test_sim_worker.py`

**Interfaces:**
- Consumes: `simulator.world.WorldState`, `simulator.compose.Message`, `actions.models.ActionItem`.
- Produces: `WIRED_FINDING_TYPES: frozenset[str]` — the curated feedback subset.
- Produces: `apply_message(world, msg, rng, cfg) -> str | None` — mutates the world, sets `msg.applied` and `msg.row_ref`, returns the row ref or `None` when the message changes nothing.
- Produces: `apply_approved(world, items, rng, cfg) -> list[dict]` — each dict `{"action_id", "finding_type", "case_id", "outcome"}` where outcome is `"applied"`, `"failed"` or `"unwired"`.
- Produces: `next_stage(world, case, cfg) -> str | None` and `advance_case(world, case, cfg, rng, *, status="done") -> str | None`. `advance_case` is public because `simulator/step.py` calls it during the drift phase.

`apply_approved` consumes `ActionItem`s whose `status` is one of `approved`, `assigned`, `in_progress`. It must **never** act on `normalise_status_values` — that template belongs to the remediation executor (CLAUDE.md §4a) and double-handling it would let an operational approval rewrite status columns.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sim_worker.py`:

```python
import random

from actions.models import ActionItem
from simulator.compose import Message
from simulator.profiles import profile_config
from simulator.worker import (WIRED_FINDING_TYPES, apply_approved,
                              apply_message, next_stage)
from simulator.world import day0_from_generator

CFG = profile_config("advisory")


def _world():
    return day0_from_generator("advisory")


def _item(finding_type, case_ids, *, status="approved",
          template=None) -> ActionItem:
    return ActionItem(
        action_id=f"A-{finding_type}", profile="advisory",
        finding_key=f"{finding_type}::x::y", finding_type=finding_type,
        title="t", summary="s", workflow="Lead-to-cash", stage="Lead",
        affected_case_ids=list(case_ids), status=status,
        action_template=template, created_at="2026-07-20",
        updated_at="2026-07-20")


def _msg(intent, case_id):
    return Message(msg_id="M001-00", day=1, sent_at="", sender="X",
                   subject="s", body="b", intent=intent, case_id=case_id)


def test_wired_set_matches_the_profile_probabilities():
    assert WIRED_FINDING_TYPES == frozenset(CFG["effect_prob"])


def test_progress_update_advances_the_case_one_stage():
    w = _world()
    cid = next(c for c, k in w.cases.items()
               if k.stage not in CFG["terminal_stages"])
    before = w.cases[cid].stage
    expected = next_stage(w, w.cases[cid], CFG)
    ref = apply_message(w, _msg("progress_update", cid), random.Random(1), CFG)
    assert w.cases[cid].stage == expected != before
    assert ref and ref.endswith(cid)


def test_client_query_changes_status_but_not_stage():
    w = _world()
    cid = next(iter(w.cases))
    before = w.cases[cid].stage
    n_before = len(w.cases[cid].events)
    apply_message(w, _msg("client_query", cid), random.Random(1), CFG)
    assert w.cases[cid].stage == before
    assert len(w.cases[cid].events) == n_before + 1


def test_new_enquiry_creates_a_case_at_the_first_stage():
    w = _world()
    n = len(w.cases)
    apply_message(w, _msg("new_enquiry", None), random.Random(1), CFG)
    assert len(w.cases) == n + 1
    newest = max(w.cases.values(), key=lambda c: c.last_ts)
    assert newest.stage == CFG["first_stage"]


def test_approved_unowned_case_gets_an_owner():
    w = _world()
    cid = next(c for c, k in w.cases.items() if k.owner == "")
    out = apply_approved(w, [_item("unowned_case", [cid])],
                         random.Random(1), CFG)
    assert out[0]["outcome"] == "applied"
    assert w.cases[cid].owner != ""


def test_unapproved_items_are_ignored():
    w = _world()
    cid = next(c for c, k in w.cases.items() if k.owner == "")
    out = apply_approved(w, [_item("unowned_case", [cid], status="proposed")],
                         random.Random(1), CFG)
    assert out == []
    assert w.cases[cid].owner == ""


def test_unwired_finding_types_are_recorded_not_acted_on():
    w = _world()
    cid = next(iter(w.cases))
    n_before = len(w.cases[cid].events)
    out = apply_approved(w, [_item("delay", [cid])], random.Random(1), CFG)
    assert out and out[0]["outcome"] == "unwired"
    assert len(w.cases[cid].events) == n_before


def test_machine_executable_template_is_never_touched():
    """normalise_status_values belongs to the remediation executor."""
    w = _world()
    cid = next(iter(w.cases))
    n_before = len(w.cases[cid].events)
    out = apply_approved(
        w, [_item("messy_status", [cid], template="normalise_status_values")],
        random.Random(1), CFG)
    assert all(o["outcome"] != "applied" for o in out)
    assert len(w.cases[cid].events) == n_before


def test_effects_are_probabilistic_not_guaranteed():
    """Over many draws at p=0.5 both outcomes must occur, or the simulator is
    authored to flatter the product."""
    outcomes = set()
    for s in range(40):
        w = _world()
        cid = next(c for c, k in w.cases.items()
                   if k.stage not in CFG["terminal_stages"])
        out = apply_approved(w, [_item("unrealised_value", [cid])],
                             random.Random(s), CFG)
        outcomes.add(out[0]["outcome"])
    assert outcomes == {"applied", "failed"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simulator.worker'`

- [ ] **Step 3: Write the implementation**

Create `simulator/worker.py`:

```python
"""The SME's staff.

Two jobs: type inbound messages into the spreadsheets, and act on the action
items a human approved. The second is the arrow that makes outcome measurement
possible — without it the world never responds to the product and affected-case
counts can only grow.

Effects are probabilistic. Some approved actions fail, because a simulator in
which every approved fix works is authored to flatter the product.
"""

from __future__ import annotations

import random

from actions.models import MACHINE_EXECUTABLE_TEMPLATES
from simulator.world import WorldState

# Finding types with a modelled worker effect. Everything else is inert and
# reported as "unwired", so coverage can be stated honestly in the write-up.
WIRED_FINDING_TYPES = frozenset({
    "stage_sla_breach", "stalled_case", "unowned_case",
    "unrealised_value", "overloaded_owner", "key_person_dependency",
})

_ACTIONABLE_STATUSES = {"approved", "assigned", "in_progress"}
_DONE = "done"
_OPEN = "with client"


def _people(world: WorldState) -> list[str]:
    seen = {e.actor for c in world.cases.values() for e in c.events if e.actor}
    return sorted(seen)


def next_stage(world: WorldState, case, cfg: dict) -> str | None:
    order = cfg["stage_order"]
    try:
        i = order.index(case.stage)
    except ValueError:
        return None
    return order[i + 1] if i + 1 < len(order) else None


def advance_case(world: WorldState, case, cfg: dict, rng: random.Random,
                 *, status: str = _DONE) -> str | None:
    """Move a case on one stage. Public — step.py's drift phase calls it."""
    nxt = next_stage(world, case, cfg)
    if nxt is None:
        return None
    actor = case.owner or rng.choice(_people(world) or [""])
    case.add(nxt, world.current_date, actor, status)
    return f"{case.cid}"


def apply_message(world: WorldState, msg, rng: random.Random,
                  cfg: dict) -> str | None:
    """Type one message into the sheets. Returns a row ref, or None if the
    message changed nothing."""
    ref = None

    if msg.intent == "new_enquiry":
        cid = cfg["case_id_fmt"].format(world.next_case_num)
        world.next_case_num += 1
        from simulator.world import SimCase
        case = SimCase(cid=cid, client=msg.sender, value=0.0)
        case.add(cfg["first_stage"], world.current_date,
                 rng.choice(_people(world) or [""]), _OPEN)
        world.cases[cid] = case
        world.intent.setdefault("arrivals", {})[cid] = world.day
        ref = cid

    elif msg.case_id and msg.case_id in world.cases:
        case = world.cases[msg.case_id]
        if msg.intent == "progress_update":
            ref = advance_case(world, case, cfg, rng)
        elif msg.intent == "payment_made":
            terminal = cfg["terminal_stages"][-1]
            if case.stage != terminal:
                case.add(terminal, world.current_date, case.owner or "", _DONE)
                ref = case.cid
        elif msg.intent == "scope_change":
            order = cfg["stage_order"]
            i = order.index(case.stage) if case.stage in order else 0
            if i > 0:
                case.add(order[i - 1], world.current_date,
                         case.owner or "", _OPEN)
                ref = case.cid
        elif msg.intent == "client_query":
            case.add(case.stage, world.current_date, case.owner or "", _OPEN)
            ref = case.cid

    msg.applied = ref is not None
    msg.row_ref = ref
    return ref


def _effect(world: WorldState, case, finding_type: str, cfg: dict,
            rng: random.Random) -> bool:
    if finding_type in ("stage_sla_breach", "stalled_case"):
        return advance_case(world, case, cfg, rng) is not None
    if finding_type == "unowned_case":
        if case.owner:
            return True
        case.add(case.stage, world.current_date,
                 rng.choice(_people(world) or ["Unassigned"]), _OPEN)
        return True
    if finding_type == "unrealised_value":
        terminal = cfg["terminal_stages"][-1]
        if case.stage == terminal:
            return True
        case.add(terminal, world.current_date, case.owner or "", _DONE)
        return True
    if finding_type in ("overloaded_owner", "key_person_dependency"):
        others = [p for p in _people(world) if p != case.owner]
        if not others:
            return False
        case.add(case.stage, world.current_date, rng.choice(others), _OPEN)
        return True
    return False


def apply_approved(world: WorldState, items, rng: random.Random,
                   cfg: dict) -> list[dict]:
    """Act on approved action items. Returns one record per item considered."""
    out: list[dict] = []
    for item in items:
        if item.status not in _ACTIONABLE_STATUSES:
            continue
        if item.action_template in MACHINE_EXECUTABLE_TEMPLATES:
            # Owned by the remediation executor (CLAUDE.md §4a). Never here.
            out.append({"action_id": item.action_id,
                        "finding_type": item.finding_type,
                        "case_id": None, "outcome": "unwired"})
            continue

        if item.finding_type not in WIRED_FINDING_TYPES:
            delta = cfg["process_param_delta"].get(item.finding_type)
            if delta:
                floor = cfg["param_floor"]
                for key, d in delta.items():
                    world.params[key] = max(floor, world.params.get(key, 0) + d)
                outcome = "applied"
            else:
                outcome = "unwired"
            out.append({"action_id": item.action_id,
                        "finding_type": item.finding_type,
                        "case_id": None, "outcome": outcome})
            continue

        p = cfg["effect_prob"][item.finding_type]
        for cid in item.affected_case_ids:
            case = world.cases.get(cid)
            if case is None:
                continue
            ok = rng.random() < p and _effect(world, case, item.finding_type,
                                              cfg, rng)
            out.append({"action_id": item.action_id,
                        "finding_type": item.finding_type,
                        "case_id": cid,
                        "outcome": "applied" if ok else "failed"})
    world.intent.setdefault("effects", []).extend(out)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_worker.py -v`
Expected: 9 passed.
If `test_effects_are_probabilistic_not_guaranteed` fails because all 40 draws land the same way, widen the loop to 200 seeds before touching the probability — `unrealised_value` is configured at 0.50 and both outcomes must be reachable.

- [ ] **Step 5: Commit**

```bash
git add simulator/worker.py tests/test_sim_worker.py
git commit -m "Add the worker: messages and approved actions become row changes"
```

---

### Task 8: `advance()` — the five-phase day

**Files:**
- Create: `simulator/step.py`
- Test: `tests/test_sim_step.py`

**Interfaces:**
- Consumes: everything from Tasks 3-7.
- Produces: `DayResult` dataclass — `day: int`, `date: str`, `messages: list[Message]`, `row_changes: list[str]`, `effects: list[dict]`, `files: list[str]`; plus `to_dict()`.
- Produces: `advance(world, approved, *, drive_dir, cache_dir, use_llm=False) -> DayResult`. Mutates `world` in place: increments `world.day` **first**, so all events written during the day carry the new date.

Phase order is fixed: arrivals and inbound (compose), worker application of messages, worker application of approved actions, drift, render.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sim_step.py`:

```python
import random

from simulator.profiles import profile_config
from simulator.step import advance
from simulator.world import day0_from_generator

CFG = profile_config("advisory")


def _run(tmp_path, days=3, approved=()):
    w = day0_from_generator("advisory")
    results = []
    for _ in range(days):
        results.append(advance(w, list(approved), drive_dir=tmp_path / "drive",
                               cache_dir=tmp_path / "cache", use_llm=False))
    return w, results


def test_advance_moves_the_clock_and_renders(tmp_path):
    w, results = _run(tmp_path, days=2)
    assert w.day == 2
    assert [r.day for r in results] == [1, 2]
    assert (tmp_path / "drive" / "leads.xlsx").exists()


def test_events_written_during_a_day_carry_that_day(tmp_path):
    w, _ = _run(tmp_path, days=1)
    newest = max(e.ts for c in w.cases.values() for e in c.events)
    assert newest.date() == w.current_date.date()


def test_two_runs_with_the_same_seed_produce_the_same_world(tmp_path):
    a, _ = _run(tmp_path / "a", days=4)
    b, _ = _run(tmp_path / "b", days=4)
    assert a.to_dict() == b.to_dict()


def test_drift_only_touches_cases_nothing_else_moved(tmp_path):
    """A stalled case must not also receive a drift event on the same day."""
    w, results = _run(tmp_path, days=5)
    for case in w.cases.values():
        by_day = {}
        for e in case.events:
            by_day[e.ts] = by_day.get(e.ts, 0) + 1
        assert max(by_day.values()) <= 2, case.cid


def test_day_result_serialises(tmp_path):
    _, results = _run(tmp_path, days=1)
    d = results[0].to_dict()
    assert set(d) >= {"day", "date", "messages", "row_changes", "effects",
                      "files"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_step.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simulator.step'`

- [ ] **Step 3: Write the implementation**

Create `simulator/step.py`:

```python
"""One sim day, committed atomically.

    arrivals -> inbound -> worker(messages) -> worker(approved) -> drift
             -> render

The day is the unit of truth: world state and the spreadsheet write happen
once, at the boundary. A UI may drip the day's messages out on a timer, but
nothing downstream ever sees a half-applied day.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from simulator import compose as compose_mod
from simulator import intents as intents_mod
from simulator import render as render_mod
from simulator import worker as worker_mod
from simulator.profiles import profile_config
from simulator.world import WorldState


@dataclass
class DayResult:
    day: int
    date: str
    messages: list = field(default_factory=list)
    row_changes: list = field(default_factory=list)
    effects: list = field(default_factory=list)
    files: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"day": self.day, "date": self.date,
                "messages": [m.to_dict() for m in self.messages],
                "row_changes": list(self.row_changes),
                "effects": list(self.effects), "files": list(self.files)}


def _drift(world: WorldState, cfg: dict, rng: random.Random,
           touched: set[str]) -> list[str]:
    """Cases nobody touched today age, and sometimes move on their own."""
    moved = []
    for cid, case in list(world.cases.items()):
        if cid in touched:
            continue
        if case.stage in cfg["terminal_stages"]:
            continue
        stall = world.params.get(f"stall_prob.{case.stage}", 0.5)
        if rng.random() < stall:
            continue
        if worker_mod.advance_case(world, case, cfg, rng):
            moved.append(cid)
    return moved


def advance(world: WorldState, approved, *, drive_dir, cache_dir,
            use_llm: bool = False) -> DayResult:
    cfg = profile_config(world.profile)
    world.day += 1
    rng = world.rng_for_day(world.day)

    picks = intents_mod.choose(world, rng, cfg)
    messages = [
        compose_mod.compose(intent, day=world.day, seq=i,
                            case=world.cases.get(cid) if cid else None,
                            rng=rng, cache_dir=Path(cache_dir),
                            use_llm=use_llm, seed=world.seed,
                            start_date=world.start_date)
        for i, (intent, cid) in enumerate(picks)]

    row_changes, touched = [], set()
    for msg in messages:
        ref = worker_mod.apply_message(world, msg, rng, cfg)
        if ref:
            row_changes.append(ref)
            touched.add(ref)

    effects = worker_mod.apply_approved(world, list(approved), rng, cfg)
    touched.update(e["case_id"] for e in effects
                   if e["outcome"] == "applied" and e["case_id"])
    row_changes.extend(e["case_id"] for e in effects
                       if e["outcome"] == "applied" and e["case_id"])

    row_changes.extend(_drift(world, cfg, rng, touched))

    files = render_mod.render(world, Path(drive_dir), rng)

    return DayResult(day=world.day, date=world.current_date.date().isoformat(),
                     messages=messages, row_changes=row_changes,
                     effects=effects, files=files)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_step.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add simulator/step.py tests/test_sim_step.py
git commit -m "Add advance(): the five-phase simulator day"
```

---

### Task 9: CLI

**Files:**
- Create: `simulator/cli.py`
- Test: `tests/test_sim_cli.py`

**Interfaces:**
- Produces: `python -m simulator.cli --profile advisory --advance N [--llm] [--approved-from <path>]`, `--reset`, `--status`. Emits one JSON object on stdout.
- Produces: `load_world(profile) -> WorldState` (day 0 if no state file), `save_world(world) -> Path`, `append_inbox(profile, messages) -> None`, `approved_items(profile, path=None) -> list[ActionItem]`.

`approved_items` reads the product's action store via `actions.store.load_actions` and filters to actionable statuses. This is the **only** place the simulator reads product state, and it is read-only.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sim_cli.py`:

```python
import json

from simulator.cli import main


def _run(argv, capsys):
    main(argv)
    return json.loads(capsys.readouterr().out)


def test_reset_writes_day_zero_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("config.DATA_SIM", tmp_path)
    out = _run(["--profile", "advisory", "--reset"], capsys)
    assert out["day"] == 0
    assert (tmp_path / "advisory" / "state.json").exists()


def test_advance_persists_state_across_invocations(tmp_path, monkeypatch,
                                                   capsys):
    monkeypatch.setattr("config.DATA_SIM", tmp_path)
    _run(["--profile", "advisory", "--reset"], capsys)
    _run(["--profile", "advisory", "--advance", "2"], capsys)
    out = _run(["--profile", "advisory", "--advance", "1"], capsys)
    assert out["day"] == 3
    assert json.loads((tmp_path / "advisory" / "state.json")
                      .read_text(encoding="utf-8"))["day"] == 3


def test_advance_writes_the_drive_and_the_inbox(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("config.DATA_SIM", tmp_path)
    _run(["--profile", "advisory", "--reset"], capsys)
    _run(["--profile", "advisory", "--advance", "3"], capsys)
    assert (tmp_path / "advisory" / "drive" / "leads.xlsx").exists()
    lines = (tmp_path / "advisory" / "inbox.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert lines and all(json.loads(ln)["msg_id"] for ln in lines)


def test_status_does_not_advance_the_clock(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("config.DATA_SIM", tmp_path)
    _run(["--profile", "advisory", "--reset"], capsys)
    _run(["--profile", "advisory", "--advance", "2"], capsys)
    out = _run(["--profile", "advisory", "--status"], capsys)
    assert out["day"] == 2
    assert out["cases"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simulator.cli'`

- [ ] **Step 3: Write the implementation**

Create `simulator/cli.py`:

```python
"""Simulator entry point.

    python -m simulator.cli --profile advisory --reset
    python -m simulator.cli --profile advisory --advance 7
    python -m simulator.cli --profile advisory --status

One JSON object on stdout per invocation, so a FastAPI orchestrator can shell
out to this exactly as it already shells out to the pipeline. The headless eval
calls advance() directly and shares the same code path.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
from pathlib import Path

import config
from actions import store
from simulator.step import advance
from simulator.world import WorldState, day0_from_generator, from_dict

_ACTIONABLE = {"approved", "assigned", "in_progress"}


def _paths(profile: str) -> dict[str, Path]:
    root = Path(config.DATA_SIM) / profile
    return {"root": root, "state": root / "state.json",
            "drive": root / "drive", "inbox": root / "inbox.jsonl",
            "cache": root / "cache"}


def load_world(profile: str) -> WorldState:
    state = _paths(profile)["state"]
    if state.exists():
        return from_dict(json.loads(state.read_text(encoding="utf-8")))
    return day0_from_generator(profile)


def save_world(world: WorldState) -> Path:
    path = _paths(world.profile)["state"]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(fd)
    Path(tmp).write_text(json.dumps(world.to_dict(), indent=2),
                         encoding="utf-8")
    os.replace(tmp, path)
    return path


def append_inbox(profile: str, messages) -> None:
    path = _paths(profile)["inbox"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for m in messages:
            fh.write(json.dumps(m.to_dict()) + "\n")


def approved_items(profile: str, path: Path | None = None) -> list:
    """Read-only peek at the product's action store — the ONLY product state
    the simulator ever reads."""
    return [i for i in store.load_actions(profile, path)
            if i.status in _ACTIONABLE]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", default="advisory")
    ap.add_argument("--advance", type=int, default=0, metavar="N")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--llm", action="store_true",
                    help="fill message slots with Claude (cached)")
    ap.add_argument("--approved-from", type=Path, default=None,
                    help="action store to read approvals from")
    args = ap.parse_args(argv)

    paths = _paths(args.profile)

    if args.reset:
        world = day0_from_generator(args.profile)
        if paths["inbox"].exists():
            paths["inbox"].unlink()
        save_world(world)
        from simulator import render as render_mod
        render_mod.render(world, paths["drive"], random.Random(world.seed))
        print(json.dumps({"profile": args.profile, "day": 0,
                          "cases": len(world.cases),
                          "drive": str(paths["drive"])}))
        return

    world = load_world(args.profile)

    if args.status or args.advance <= 0:
        print(json.dumps({"profile": args.profile, "day": world.day,
                          "date": world.current_date.date().isoformat(),
                          "cases": len(world.cases),
                          "drive": str(paths["drive"])}))
        return

    approved = approved_items(args.profile, args.approved_from)
    days = []
    for _ in range(args.advance):
        result = advance(world, approved, drive_dir=paths["drive"],
                         cache_dir=paths["cache"], use_llm=args.llm)
        append_inbox(args.profile, result.messages)
        days.append(result.to_dict())
    save_world(world)

    print(json.dumps({"profile": args.profile, "day": world.day,
                      "date": world.current_date.date().isoformat(),
                      "cases": len(world.cases),
                      "drive": str(paths["drive"]), "days": days}))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_cli.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add simulator/cli.py tests/test_sim_cli.py
git commit -m "Add the simulator CLI: reset, advance, status"
```

---

### Task 10: End-to-end — the loop actually closes

This is the task that proves P1 did its job. The product ingests a simulated drive, produces findings, and an approved action visibly reduces the affected-case count at a later day. That reduction is exactly what `actions/outcome.py::compare` needs and could never see in the pre-baked stream.

**Files:**
- Test: `tests/test_sim_e2e.py`
- Modify: `HANDOVER.md` (add a "Running the simulator" section)

**Interfaces:**
- Consumes: `simulator.step.advance`, `readers.mapped_reader.read_mapped`, and `detection.case_rules.detect_case_findings(df, profile_cfg, *, as_of=None) -> list[CaseFinding]`. A `CaseFinding` carries `.type` (the rule id, e.g. `"unowned_case"`), `.affected_cases: list[str]` and `.case_details: list[dict]`. Note it also carries `.id` (`CF001..N`), assigned by rank order — never join on it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sim_e2e.py`:

```python
import json
import random

import pandas as pd

import config
from actions.models import ActionItem
from detection.case_rules import detect_case_findings
from simulator.profiles import profile_config
from simulator.step import advance
from simulator.world import day0_from_generator

CFG = profile_config("advisory")
PROFILE_CFG = config.MESSY_PROFILES["advisory"]


def _frame(world) -> pd.DataFrame:
    """The world as the product would see it after ingest — same columns the
    case rules read, without going through Excel."""
    rows = [{"case_id": c.cid, "activity": e.stage, "ts": e.ts,
             "actor": e.actor, "status": e.status,
             "source_ref": f"sim:{c.cid}", "value": c.value}
            for c in world.cases.values() for e in c.events]
    return pd.DataFrame(rows)


def _unowned(df) -> set[str]:
    found = detect_case_findings(df, PROFILE_CFG)
    return {cid for f in found if f.type == "unowned_case"
            for cid in f.affected_cases}


def _item(finding_type, case_ids) -> ActionItem:
    return ActionItem(
        action_id="A-1", profile="advisory",
        finding_key=f"{finding_type}::x::y", finding_type=finding_type,
        title="t", summary="s", workflow="Lead-to-cash", stage="Lead",
        affected_case_ids=sorted(case_ids), status="approved",
        created_at="2026-07-20", updated_at="2026-07-20")


def test_approving_an_action_reduces_the_finding_the_product_reports(tmp_path):
    world = day0_from_generator("advisory")
    before = _unowned(_frame(world))
    assert before, "expected unowned cases in the day-0 world"

    approved = [_item("unowned_case", before)]
    for _ in range(3):
        advance(world, approved, drive_dir=tmp_path / "drive",
                cache_dir=tmp_path / "cache", use_llm=False)

    after = _unowned(_frame(world))
    assert len(after) < len(before), (
        "an approved unowned_case action must reduce the count the product "
        "reports — this is the arrow the pre-baked stream never had")


def test_doing_nothing_does_not_reduce_it(tmp_path):
    """The control: without the approval, the count must not fall for the same
    reason. Otherwise the reduction above proves nothing."""
    world = day0_from_generator("advisory")
    before = _unowned(_frame(world))
    for _ in range(3):
        advance(world, [], drive_dir=tmp_path / "drive",
                cache_dir=tmp_path / "cache", use_llm=False)
    after = _unowned(_frame(world))
    assert len(after) >= len(before) - 1, (
        "unowned cases should not clear themselves without intervention")


def test_the_rendered_drive_is_ingestable_by_the_product(tmp_path):
    from audit.schemas import ApprovedFileMapping, ApprovedMapping
    from readers.mapped_reader import read_mapped

    world = day0_from_generator("advisory")
    advance(world, [], drive_dir=tmp_path / "drive",
            cache_dir=tmp_path / "cache", use_llm=False)

    gt = json.loads(config.MESSY_PROFILES["advisory"]["gt_mapping"]
                    .read_text(encoding="utf-8"))
    approved = ApprovedMapping(
        profile="advisory", approved_at="2026-01-01T00:00:00+00:00",
        source_proposal_generated_at="2026-01-01T00:00:00+00:00",
        files=[ApprovedFileMapping(**f) for f in gt["files"]])

    events, _docs = read_mapped(tmp_path / "drive", approved)
    assert events, "the simulated drive must read through the approved mapping"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_e2e.py -v`
Expected: FAIL on `test_approving_an_action_reduces_the_finding_the_product_reports` — the whole point of the task is that this assertion is the one that could never hold before.

- [ ] **Step 3: Make it pass**

If it fails, the fault is in `simulator/worker.py::_effect`'s `unowned_case` branch, not the test: the event it appends must carry a **non-empty** actor at the case's **current** stage, or `detection/case_rules.py` still reads the case as unowned. Do not change `detection/case_rules.py` to fit the test.

Run: `.venv/Scripts/python.exe -m pytest tests/test_sim_e2e.py -v`
Expected: 3 passed

- [ ] **Step 4: Run the full suite**

Run: `PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -q`
Expected: all green — 202 pre-existing plus roughly 45 new.

- [ ] **Step 5: Document how to run it**

Add to `HANDOVER.md`, as a new section before the existing troubleshooting notes:

```markdown
## Running the simulator (System 2)

The simulator generates the SME's operational reality; the product reads the
drive it writes. They share nothing else.

    # start a fresh world at day 0 and render it
    .venv/Scripts/python.exe -m simulator.cli --profile advisory --reset

    # advance a week; approvals in the action store change what happens
    .venv/Scripts/python.exe -m simulator.cli --profile advisory --advance 7

    # analyse the simulated drive with the unchanged product
    .venv/Scripts/python.exe ingest.py --source messy --profile advisory \
        --drive data/sim/advisory/drive

Add `--llm` to fill message wording with Claude (cached per seed/day/message,
so a replay is free and offline). Without it, deterministic templates are used
and nothing calls out.

State lives in `data/sim/advisory/`: `state.json` (world + day + params),
`drive/` (what the product ingests), `inbox.jsonl` (every message), `cache/`
(LLM fills). Delete the directory to start over.
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_sim_e2e.py HANDOVER.md
git commit -m "Prove the loop closes: an approved action reduces what the product reports"
```

---

## Not in this plan

- **P2 — eval rewire.** Retargeting `eval/replay.py` onto the simulator, regenerating the replay artefacts, re-citing CLAUDE.md §7, and retiring `synthetic/generate_stream.py`, `stream_<p>/`, `ground_truth_stream_*.json` and `messy_advisory_followup/`.
- **P3 — dashboard demo mode.** The Demo button, the clock, the inbox sidebar, the sheet grid and the vitals wiring.
- **Ollama removal** and the CLAUDE.md §4/§6/§6a/§9 edits it forces.
- **The control arm** (same seed, interventions disabled), parked as optional.

Nothing in this plan deletes an existing data path. The retirement list in the
spec executes in P2, once the simulator has replaced what those paths provided.
