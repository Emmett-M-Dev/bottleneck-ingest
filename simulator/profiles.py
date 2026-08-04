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

    # Probability that an approved PROCESS intervention actually lands.
    # Same rule as effect_prob: deliberately below 1.0, so this path cannot
    # be the one place in the simulator where every approval magically works.
    "process_effect_prob": {"delay": 0.60, "repetition": 0.50, "rework": 0.50},
    "param_floor": 0.02,

    # How compose.py describes the SME to the LLM when filling message slots
    # (simulator/compose.py's prompt). Engine code must never name a
    # business type itself.
    "business_description": "a small consultancy",

    # Deterministic (non-LLM) filler for a message's "what this is about"
    # slot — and the fallback whenever the LLM path is off/unavailable.
    "fallback_details": [
        "the March intake", "the reporting pack", "next quarter's phasing",
        "the site visit dates", "the draft findings", "the onboarding pack",
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
