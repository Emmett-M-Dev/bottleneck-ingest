# Human-in-the-Loop

The system suggests. A person decides. This holds at two gates.

## Why two gates

Most systems have one approval step. This one has two, because there are two moments where trust matters:

- The system must trust the data before it reasons over it.
- The firm must trust a fix before it runs.

So there are two gates, in order.

## Gate 1: Mapping Review

This gate runs first, at ingestion.

The mapping agent proposes how each messy column maps to the clean schema. It also flags the problems it found. The user reviews the proposal in the dashboard. The user confirms each mapping or edits it.

Nothing downstream trusts the data until the user approves. The system logs the corrections and the time to decide. These logs are the measurable human-in-the-loop numbers.

## Gate 2: Fixes

This gate runs later, at execution.

For each bottleneck, the dashboard shows the diagnosis, the suggested fix, the evidence behind it, and the cost estimate. The user approves, rejects, or modifies each fix.

No fix runs without an explicit decision. An approved or edited fix also feeds the learning loop, so the system retrieves it next time. See [RAG Diagnosis](RAG-Diagnosis).

## The dashboard

A React dashboard drives both gates. It shows:

- The Mapping Review for Gate 1.
- A pipeline stepper and a workflow diagram.
- The bottlenecks, including the anomaly cards and the RAG evidence.
- The Fixes tab for Gate 2, and the remediation diff.
- A human-in-the-loop metrics strip.
- An impact panel with history.
- A view of exactly what the model saw, with the scrubbed payload.

A backend acts as a thin bridge. It shells out to the pipeline and returns plain JSON. The dashboard holds no heavy libraries.

## How the gate pauses the run

The graph treats Gate 2 as a stop point. A fresh run pauses there and writes its state to a file. The user decides in the dashboard. A resume run reads the decision and carries on from the gate.

This is a two-phase run. There is no hidden state. The run-state file is the audit record. Every decision is logged with a timestamp.

## Why this matters

Keeping a human at both gates puts accountability with the firm, not the model. The system shows its working and its evidence at every step. This fits the ACM Code of Ethics on human oversight. See [Privacy and Ethics](Privacy-and-Ethics).
