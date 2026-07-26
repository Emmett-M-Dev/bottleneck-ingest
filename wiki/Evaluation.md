# Evaluation

The project tests each claim against the seeded ground truth. All numbers below come from repeatable scripts in the repo.

## Mapping accuracy (the headline result)

This tests the mapping agent. It scores how well the system maps messy columns to the clean schema. The score is an F1 across three conditions: a heuristic baseline, the LLM agent, and the human-approved result.

| Firm | Baseline F1 | LLM F1 | Human F1 |
|---|---|---|---|
| foyle | 0.846 | 0.968 | 1.000 |
| joinery | 0.308 | 0.909 | 1.000 |

Read the joinery row first. The baseline scores 0.308 because the joinery firm renames its headers, and a naive match cannot follow the change. The LLM agent lifts this to 0.909. The human gate closes the rest.

This gap is the argument for the LLM agent. A simple rule breaks on renamed headers. The agent does not.

## Detection accuracy

This tests the bottleneck detector. It scores precision, recall, and F1 per pattern type against the seeded ground truth. It compares the marker baseline with the dynamic detector.

| Firm | Baseline macro-F1 | Dynamic macro-F1 |
|---|---|---|
| foyle | 0.524 | 1.000 |
| joinery | 0.523 | 1.000 |

The baseline collapses on structural repetition and rework. Presence alone cannot see a loop or a duplicate. The dynamic detector finds the structure and scores a perfect macro-F1 on this synthetic data.

This mirrors the mapping result: a simple rule fails where a structural method holds.

## Longitudinal replay

This tests the system over time, not on one snapshot.

The replay runs nine weekly snapshots per firm through the unchanged core. A simulated approver stands in for the human at Gate 2, so the learning loop runs end to end. The write-up states plainly that this approver is an oracle, not a real person.

Two curves come out:

- **Detection F1 over time.** The detector tracks a moving ground truth. At one week for the joinery firm, a gap threshold wobbles. Precision dips, the gate rejects the false positives, and F1 recovers. The project reports this honestly rather than hiding it.
- **Learned-fix retrieval.** The rate climbs from zero to one as approved fixes enter the store. This shows the learning loop working: the system retrieves its own approved fixes in later weeks.

The replay writes its results to the outputs folder. It never touches the dashboard's real state. A fresh reset keeps each run repeatable.

## Other measures

- **RAG retrieval quality.** Retrieval relevance, measured with MRR or NDCG, where a relevant hit matches both the firm and the bottleneck type.
- **Human-in-the-loop metrics.** Correction counts and time to decide, logged at both gates.
- **Qualitative review.** A planned structured walkthrough with two or three experts, on trust, usability, and recommendation quality.

## Honest limitations

The project states its limits:

- The data is synthetic. One person designed the injection and the evaluation. The circularity guard reduces this risk but does not remove it. See [Data Strategy](Data-Strategy).
- The timing data comes from development runs, not a controlled user study.
- The replay approver is an oracle, not a real user.

These limits are stated in the write-up, not hidden.
