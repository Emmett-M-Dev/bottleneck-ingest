# Data Strategy

## Synthetic first

All data in this repo is synthetic. The system uses no live company data. This keeps the build safe to share and safe to test.

There is one messy drive per firm:

- `messy_foyle` — an educational-placement firm.
- `messy_joinery` — a trades and fit-out firm.

Each drive is a folder of deliberately messy spreadsheets. Column names drift. Statuses are free text. Files repeat data. This is on purpose. It mirrors how small firms really store data.

## Ground truth by design

The system injects three bottleneck patterns on purpose: delay, repetition, and rework. Because the code injects them, the ground truth is known before detection runs. The patterns are structural, not labels:

- **Delay** — an outlier gap between stages.
- **Repetition** — a literal duplicate stage entry.
- **Rework** — a genuine backward transition.

This gives a clean test. The system knows what it planted, so it can score what detection finds.

## The circularity guard

There is a risk when one person builds the data and also builds the detector. The detector could just learn the injection rules. That would prove nothing.

The project guards against this. The injection code and the detection code are kept apart. The detector does not know the injection rules. It scans for structure, not for planted labels. So a correct detection is a real result, not a leak.

## Two contrasting firms

The two firms are different on purpose. One places students; one fits out buildings. Their spreadsheets look nothing alike. The joinery firm even renames its headers in a way that breaks a naive baseline.

Running both through one core is the test of generalisability. If the same core handles both, the approach holds beyond one firm.

## Longitudinal replay

A single snapshot shows one moment. Real firms change week to week. So the project also tests the system over time.

A generator writes nine weekly snapshots per firm, plus a ground truth for each week. A replay harness runs each week through the unchanged core. It scores detection against a moving truth. It also feeds approvals into the learning loop, so the system retrieves its own approved fixes in later weeks.

This shows the system as a dynamic thing, not a one-shot test. The [Evaluation](Evaluation) page reports the curves.
