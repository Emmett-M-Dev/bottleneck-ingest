"""Generate the synthetic resolution corpus — the RAG knowledge base.

One JSON file per SME profile (data/synthetic/resolutions_<profile>.json),
26 entries each: 6 per bottleneck type (delay / repetition / rework) that a
diagnosis should retrieve, plus 5 near-miss distractors (same domain, wrong
pattern, bottleneck_type "other") and 3 irrelevant ops entries — so top-3
retrieval is non-trivial and a later MRR/NDCG eval has known relevance
(relevant = profile AND bottleneck_type both match).

Resolution text is hand-authored and PII-free by construction: staff appear as
roles ("the placements coordinator", "a fitter"), suppliers and partners as
generic descriptions — nothing here needs scrubbing before it reaches an API
payload. Outcomes are mostly successes with a couple of partial/failed ones
per type, because a real resolution log is not a highlight reel.

Only days_to_resolve and the source label are randomised (seeded), so re-runs
produce byte-identical files. Run:  python synthetic/generate_resolutions.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

SEED = 41

SOURCES = ["ops_log_2024", "ops_log_2025", "manager_interview",
           "consultant_report", "staff_handover_notes"]

# How long implementing each kind of fix plausibly took, in days.
DAYS_RANGE = {"delay": (5, 30), "repetition": (7, 21),
              "rework": (7, 28), "other": (2, 21)}

# Per profile: {type: (stage, [(problem, action, outcome), ...])} for the three
# real bottleneck types, plus near_miss / irrelevant lists of
# (stage, problem, action, outcome) with bottleneck_type "other".
CORPUS: dict[str, dict] = {
    "foyle": {
        "delay": ("Booking Confirmed", [
            ("Summer bookings sat unconfirmed for over two weeks because host "
             "families were recruited only after the placement offer went out.",
             "Started host recruitment for the summer intake in February and "
             "kept a rolling list of pre-vetted families.",
             "Average time to confirmation dropped from 16 days to 5 the "
             "following season."),
            ("A batch of group bookings stalled before confirmation while one "
             "coordinator chased the same three host families.",
             "Introduced a weekly review of every booking still unconfirmed "
             "seven days after the placement offer, reallocating chases to "
             "whoever had capacity.",
             "Stalled bookings were spotted within a week instead of "
             "surfacing at invoicing."),
            ("Bookings from one sending organisation waited on confirmation "
             "because the host stipend rate had not been agreed.",
             "Agreed stipend rates with the sending organisation before the "
             "season opened and published them to the host list.",
             "Worked for that organisation, but rate queries from new "
             "partners still caused occasional stalls."),
            ("Confirmations queued behind a single inbox that only the "
             "placements coordinator checked.",
             "Set up a shared confirmations inbox monitored by two staff "
             "with an out-of-office handover rule.",
             "No booking waited more than three working days on an unread "
             "host reply."),
            ("Peak-season bookings were confirmed late because host "
             "availability was checked one family at a time by phone.",
             "Sent a single availability form to all hosts at the start of "
             "each season and recorded responses on the tracker.",
             "Matching worked from a live availability list; the phone "
             "rounds were dropped."),
            ("Several autumn bookings breached the confirmation target when "
             "two host families withdrew in the same week.",
             "Kept a standby pool of three vetted host families per season "
             "to absorb late withdrawals.",
             "Subsequent withdrawals were re-covered within two days without "
             "breaching the target."),
        ]),
        "repetition": ("Document Collection", [
            ("Student CVs and consent letters were requested a second time "
             "after the spring sheet was forked for summer bookings.",
             "Added a received-documents checklist to the booking row and "
             "carried open bookings into the new season's sheet instead of "
             "re-entering them.",
             "Document re-requests fell to near zero the following intake."),
            ("Documents arrived by email and were saved to personal folders, "
             "so colleagues re-requested files that already existed.",
             "Created one shared folder per booking reference and made "
             "saving to it part of the intake step.",
             "Staff could see at a glance what had arrived; duplicate "
             "requests stopped."),
            ("Sending organisations were asked twice for medical consent "
             "forms because the tracker had no column for them.",
             "Added a consent-form column with received dates to the "
             "tracker.",
             "The second-ask emails stopped, though older bookings still "
             "needed a manual back-fill."),
            ("A temp covering the placements desk re-requested documents "
             "because the handover notes did not say where files were kept.",
             "Wrote a one-page desk guide covering where documents live and "
             "how the checklist is read.",
             "The next cover period ran without duplicate requests."),
            ("Documents were re-requested whenever bookings were transferred "
             "between coordinators mid-season.",
             "Made the document checklist part of the transfer email "
             "template.",
             "Transfers stopped triggering re-requests."),
            ("Passport copies were requested again at pre-arrival even when "
             "collected at booking.",
             "Trialled having hosts verify passports on arrival instead of "
             "collecting copies twice.",
             "Dropped after one season — hosts found verification awkward "
             "and copies went back to being collected at booking."),
        ]),
        "rework": ("Placement Offer", [
            ("Confirmed placements were re-allocated when host families "
             "dropped out within a fortnight of arrival.",
             "Re-confirmed host availability at the point of matching rather "
             "than relying on season-start signup.",
             "Late drop-outs fell by more than half the next season."),
            ("A work-placement company withdrew after students were matched, "
             "forcing re-allocation of a whole group.",
             "Asked partner companies to confirm capacity in writing four "
             "weeks before arrival.",
             "Group withdrawals were caught early enough to re-match "
             "calmly."),
            ("Re-allocations were done under time pressure because no "
             "reserve hosts existed.",
             "Held back two vetted host families per season as a dedicated "
             "reserve.",
             "Re-allocations were absorbed without emergency phone rounds."),
            ("The same host family caused repeated re-allocations across "
             "seasons before anyone noticed the pattern.",
             "Logged a fall-through reason against the host record every "
             "time a placement was re-allocated.",
             "Repeat offenders were spotted and retired from the list."),
            ("Students were re-placed after arrival because room "
             "arrangements did not match what was promised.",
             "Added a pre-season home visit for first-time host families.",
             "Post-arrival moves became rare, though the visits stretched "
             "staff time in peak weeks."),
            ("Re-allocation emails went out without updating the tracker, so "
             "invoices were raised against the old host.",
             "Made the tracker update part of the re-allocation email "
             "checklist.",
             "Invoice corrections stopped."),
        ]),
        "near_miss": [
            ("Pre-Arrival Logistics",
             "Visa appointments for one nationality ran six weeks behind, "
             "compressing pre-arrival logistics.",
             "Moved booking deadlines earlier for the affected "
             "nationalities.",
             "Arrivals stopped landing before their paperwork cleared."),
            ("Invoice Issued",
             "Invoices went out weeks after confirmation because billing "
             "details were collected late.",
             "Collected billing details on the booking form itself.",
             "Invoices went out with the confirmation."),
            ("Arrival",
             "Airport transfers were double-booked on peak Saturdays.",
             "Moved transfer bookings onto a single shared calendar.",
             "Double-bookings stopped."),
            ("Placement Offer",
             "Placement offers went out with out-of-date host profiles "
             "attached.",
             "Regenerated host profiles from the tracker at offer time.",
             "Complaints about mismatched profiles stopped."),
            ("Pre-Arrival Logistics",
             "Welcome packs were posted too late to arrive before students "
             "flew.",
             "Switched welcome packs to email with a printed copy handed "
             "over at arrival.",
             "Every student had the pack before travelling."),
        ],
        "irrelevant": [
            ("General",
             "The office printer jammed daily during peak season.",
             "Replaced the printer and moved label printing to the front "
             "desk.",
             "Printing complaints stopped."),
            ("General",
             "Staff missed messages spread across text, email and calls.",
             "Introduced a Monday morning stand-up for the placements team.",
             "Fewer things slipped between channels."),
            ("General",
             "Season-end filing took a full week of staff time.",
             "Agreed a fixed folder structure and archived by booking "
             "reference.",
             "Filing dropped to under a day."),
        ],
    },
    "joinery": {
        "delay": ("Site Work Started", [
            ("Site work on kitchen fits waited over two weeks for toughened "
             "glass units after materials were ordered.",
             "Ordered known long-lead items the day the quote was accepted "
             "instead of at drawing sign-off.",
             "The glass wait disappeared from the schedule; fitters stopped "
             "being stood down."),
            ("Jobs stalled between materials ordering and site start because "
             "supplier lead times were guessed.",
             "Recorded actual lead times per supplier on the materials sheet "
             "and scheduled site work against them.",
             "Start dates stopped slipping; clients got honest dates at "
             "quote stage."),
            ("Spray-finished doors held up several jobs when the finishing "
             "shop got backed up.",
             "Booked spray-shop slots at order time and kept one backup "
             "finisher.",
             "Finishing delays dropped, though the backup shop cost more per "
             "unit."),
            ("A supplier's stock problem left three jobs waiting with no "
             "warning.",
             "Asked suppliers to confirm despatch dates within 48 hours of "
             "an order and flagged anything unconfirmed.",
             "Stock problems surfaced the week the order was placed instead "
             "of the week the job was due."),
            ("Common fixings ran out mid-job, pausing site work while "
             "someone drove to the merchant.",
             "Kept a buffer stock of hinges, runners and screws in the "
             "workshop, restocked monthly.",
             "Merchant runs during site work stopped."),
            ("Winter jobs waited on materials because orders were batched to "
             "save on delivery charges.",
             "Trialled batching only non-critical items while ordering "
             "critical-path materials immediately.",
             "Abandoned after a month — the delivery savings were not worth "
             "the tracking overhead, and per-job ordering returned."),
        ]),
        "repetition": ("Site Survey", [
            ("Fitters drove back to site to re-measure because the first "
             "survey missed service positions.",
             "Used a fixed survey checklist covering openings, services and "
             "access on the first visit.",
             "Re-measures fell from roughly one a fortnight to one a "
             "quarter."),
            ("The workshop queried survey dimensions and triggered repeat "
             "site visits.",
             "Photographed every opening alongside its measurements and had "
             "the workshop confirm the cutting list from the survey pack "
             "before ordering.",
             "The workshop stopped sending fitters back for missing "
             "dimensions."),
            ("Stair jobs were re-measured because floor levels were assumed "
             "level on the first visit.",
             "Added a laser-level check to the survey kit for stair and "
             "wardrobe jobs.",
             "Assumption-driven re-measures stopped on those job types."),
            ("Re-measures happened when the quoting joiner and the fitting "
             "joiner surveyed differently.",
             "Standardised the survey form so any joiner's measurements read "
             "the same.",
             "Handover between quoting and fitting stopped producing repeat "
             "visits."),
            ("Big commercial jobs needed second visits because one surveyor "
             "could not cover everything.",
             "Sent two people on surveys over a set contract value.",
             "Second visits reduced, but scheduling two people proved hard "
             "in busy months."),
            ("Clients changed opening sizes after survey, forcing "
             "re-measures.",
             "Asked clients to sign the survey sheet as the agreed baseline, "
             "with changes quoted separately.",
             "Changes still happened but were billed rather than absorbed."),
        ]),
        "rework": ("Site Work Started", [
            ("Fitters were called back for snagging after handover on nearly "
             "a third of fits.",
             "Walked the snag list with the client against the job spec "
             "before leaving site and fixed same-day items on the spot.",
             "Call-backs dropped to the odd genuine defect."),
            ("Snagging revisits went unbilled because defects were reported "
             "weeks after handover.",
             "Signed off the walk-through with the client so the invoice "
             "went out immediately and later reports were quoted as new "
             "work.",
             "The invoice lag disappeared and free revisits stopped."),
            ("Door alignment call-backs clustered in winter as timber moved "
             "after fitting.",
             "Let site-stored timber acclimatise for 48 hours before fitting "
             "in the wet months.",
             "Winter alignment call-backs roughly halved."),
            ("Revisits were wasted waiting on parts the fitter did not "
             "carry.",
             "Stocked each van with a standard snagging kit — hinges, "
             "runners, touch-up finish.",
             "Most snags were fixed in one visit."),
            ("The same fitting mistakes recurred across different fitters.",
             "Ran a monthly toolbox talk reviewing that month's snag list.",
             "Repeat categories shrank, though new starters still generated "
             "a bump each time."),
            ("Handover was skipped on small jobs, and small jobs generated "
             "the most call-backs.",
             "Made the client walk-through mandatory regardless of job "
             "size.",
             "Small-job call-backs fell in line with the rest."),
        ]),
        "near_miss": [
            ("Quote Sent",
             "Quotes sat unanswered for weeks with no follow-up.",
             "Diarised a follow-up call one week after every quote.",
             "Win rate improved and dead quotes were closed off."),
            ("Payment Received",
             "Invoices were paid thirty-plus days late by two commercial "
             "clients.",
             "Added staged payments to commercial terms.",
             "Cashflow gaps narrowed."),
            ("Site Work Started",
             "A van breakdown pushed three site starts back a week.",
             "Booked van servicing in the quiet season and listed a hire "
             "firm as backup.",
             "Breakdowns stopped cascading into the schedule."),
            ("Materials Ordered",
             "Material costs on quotes drifted as supplier prices rose.",
             "Refreshed the price list quarterly with each supplier.",
             "Quote margins stopped eroding."),
            ("Quote Accepted",
             "Clients requested design changes after acceptance, mid-build.",
             "Introduced a change-order form priced before work continued.",
             "Changes stopped stalling the workshop."),
        ],
        "irrelevant": [
            ("General",
             "Workshop racking made sheet materials slow to pull.",
             "Rebuilt racking by sheet size and labelled the bays.",
             "Pulling materials for a job takes minutes."),
            ("General",
             "First-aid cover lapsed when the one certified member left.",
             "Trained two staff and diarised recertification.",
             "Cover maintained."),
            ("General",
             "Dust extraction bags were changed mid-cut, interrupting the "
             "saw line.",
             "Moved bag changes to the morning routine.",
             "Interruptions stopped."),
        ],
    },
}

_ID_PREFIX = {"foyle": "RES-FOY", "joinery": "RES-JOI"}


def build_profile(profile: str) -> list[dict]:
    """Assemble one profile's 26 entries in a fixed order (ids are stable)."""
    spec = CORPUS[profile]
    entries: list[dict] = []

    def add(btype: str, stage: str, problem: str, action: str, outcome: str) -> None:
        lo, hi = DAYS_RANGE[btype if btype in DAYS_RANGE else "other"]
        entries.append({
            "resolution_id": f"{_ID_PREFIX[profile]}-{len(entries) + 1:03d}",
            "profile": profile,
            "bottleneck_type": btype,
            "stage": stage,
            "problem_description": problem,
            "action_taken": action,
            "outcome": outcome,
            "days_to_resolve": random.randint(lo, hi),
            "source": random.choice(SOURCES),
        })

    for btype in ("delay", "repetition", "rework"):
        stage, items = spec[btype]
        for problem, action, outcome in items:
            add(btype, stage, problem, action, outcome)
    for stage, problem, action, outcome in spec["near_miss"]:
        add("other", stage, problem, action, outcome)
    for stage, problem, action, outcome in spec["irrelevant"]:
        add("other", stage, problem, action, outcome)
    return entries


def main() -> None:
    random.seed(SEED)
    for profile in sorted(config.MESSY_PROFILES):
        entries = build_profile(profile)
        out = config.resolutions_path(profile)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        by_type: dict[str, int] = {}
        for e in entries:
            by_type[e["bottleneck_type"]] = by_type.get(e["bottleneck_type"], 0) + 1
        print(f"Wrote {len(entries):3d} resolutions -> {out} ({by_type})")


if __name__ == "__main__":
    main()
