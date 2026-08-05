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
    names = render(day0_from_generator("advisory"), tmp_path, random.Random(1))
    assert not list(tmp_path.glob("*.tmp"))
    # A leaked temp file must not merely dodge the "*.tmp" glob — it must not
    # exist under ANY name. Otherwise a survivor from a crash between mkstemp
    # and cleanup would be a legitimate-looking .xlsx that the product's
    # readers (readers/excel_reader.py, audit/scan.py) glob and ingest as a
    # real sheet, corrupting the event log with duplicate rows.
    assert {p.name for p in tmp_path.iterdir()} == set(names)


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
    # clients.xlsx/timesheets.xlsx are built by the frozen generator's
    # module-level-random builders (build_clients/build_timesheets), not from
    # `rng` directly — they only stay reproducible if render() reseeds global
    # random from `rng` before calling them.
    for n in ("leads.xlsx", "projects.xlsx", "clients.xlsx", "timesheets.xlsx"):
        left = pd.read_excel(a / n, engine="openpyxl")
        right = pd.read_excel(b / n, engine="openpyxl")
        pd.testing.assert_frame_equal(left, right)
