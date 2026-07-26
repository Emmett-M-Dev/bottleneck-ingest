# Scope and Status

## Two separate things

There are two things, and the project keeps them apart:

1. **The dissertation build (this repo).** A local, file-based system that runs on one laptop. It uses synthetic data. It holds no live credentials. This is what gets built, tested, and submitted.

2. **A live product for Foyle.** This is a later conversation. Live connectors, real credential handling, and real-time crawling belong here. None of that is in the dissertation build.

When a task looks like "connect to the real SharePoint" or "handle live OAuth against real data," it falls outside the dissertation scope.

Real firm data is used only as a one-off, consented, supervisor-signed-off export. The system never pulls live data with credentials.

## In scope

- Reading messy synthetic spreadsheets, one drive per firm.
- A mapping-inference agent that proposes schema mappings for a human to confirm.
- Dynamic bottleneck detection with no per-firm markers.
- RAG diagnosis over a store of past resolutions.
- Two human approval gates.
- A remediation step that cleans data after approval.
- A React dashboard for the human gates.

## Out of scope

- A real SharePoint or Drive crawler.
- Live credential handling.
- Deployment as a running service.

## Current status

The core is built and tested. The main milestones have shipped:

- Dynamic detection with no markers.
- RAG diagnosis with a learning loop.
- A LangGraph run that pauses at the approval gate and resumes after a decision.
- A local model for an exploratory anomaly pass.
- A cost model and an impact history.
- A React dashboard for both gates.

Generalisability is shown. Two firms — an educational-placement firm and a joinery firm — run through one core with no new reader or detector code.

The evaluation numbers are produced and repeatable. See the [Evaluation](Evaluation) page.

## Constraints

- Feature freeze: 1 August 2026. After this, only bug fixes, evaluation, and writing.
- Report submission: 24 August 2026.
- Viva: 31 August 2026.

This wiki reflects the build as it stands during the write-up phase.
