# Glossary

Plain definitions for the terms used across this wiki.

**Action layer.** The generic part that turns findings and case rules into ranked, ownable work. See [Action Layer](Action-Layer).

**ActionItem.** One thing that needs attention, with everything a worker needs to decide: evidence, affected cases, projected impact, category, owner, and due date.

**Adapter layer.** The thin, per-firm part of the system. It maps a firm's messy columns to the fixed schema. It is the only part that changes per firm.

**Bottleneck.** A stage in a workflow where work gets stuck. This project detects three types: delay, repetition, and rework.

**Canonical schema.** The one clean data shape for all firms. Every row becomes an `Event` with a case id, an activity, a timestamp, an actor, a status, and a source reference.

**Case rule.** One of six generic checks that ask which individual cases need attention: SLA breach, stalled, unowned, unrealised value, overloaded owner, key-person dependency. Thresholds come from config; the rule code holds no firm's vocabulary.

**Category.** The routing decision on an ActionItem: `data_quality`, `case_action`, or `process_intervention`. It decides whether approving writes a file or creates a tracked task.

**ChromaDB.** The vector store that holds the resolution knowledge for search.

**Circularity guard.** The rule that keeps the data-injection code apart from the detection code. It stops the detector from just learning the injection rules.

**Delay.** A bottleneck type. Cases wait too long entering or leaving a stage.

**Dynamic detection.** The detector that scans every stage and reports zero to N findings, with no marker config.

**Effective.** The verdict on whether an intervention worked. It is tri-state: yes, no, or `None` for "not enough evidence yet".

**Effect probability.** The chance that an approved action actually works inside the [Live Simulator](Live-Simulator). Deliberately below 1.0, so the simulated world cannot flatter the product.

**Event log.** The canonical data store. The clean, normalised record of what happened.

**Finding key.** A finding's stable content identity — `type :: stage :: metric_label`. Used to match a finding across two analyses. The finding's *id* cannot be, because ids are assigned by rank order.

**Gate 1, Mapping Review.** The first human gate. A person confirms the schema mapping before the system trusts the data.

**Gate 2, Actions.** The second human gate. A person approves, rejects, or dismisses each action, and sets an owner and a due date, before anything is assigned or run.

**Generalisability.** The main claim. The same core runs for different firms with no new detection code.

**Ground truth.** The known answer, planted on purpose in the synthetic data, used to score detection.

**Human-in-the-loop (HITL).** The design where a person approves the choices that matter. This system has two such gates.

**Intervention.** The commitment created when a human approves an action. It holds a baseline measurement, a success metric, an owner, and a review date.

**LangGraph.** The library that runs the pipeline steps in order and pauses at the gate.

**Learning loop.** The process where a fix that was measured to work is saved into the resolution store, so the system can retrieve it next time. It is outcome-gated, not approval-gated.

**Machine-executable.** The single predicate that authorises a file write. Only data-quality templates on the machine-safe list pass it — currently just `normalise_status_values`.

**Mapping drift.** The hazard where approving in the browser overwrites the committed approved mapping and moves the eval numbers. **Do not** recover with `git checkout mappings/approved_*.json` — a drifted mapping was itself committed once, so that restores the drift. Recover from a known-good commit: `git checkout <commit> -- mappings/approved_<profile>.json`.

**Mapping-inference agent.** The agent that reads messy files and proposes how each column maps to the schema.

**Observation.** A real measurement taken from an analysis snapshot. The opposite of a projection, and kept in a separate field from one.

**Ollama.** A tool for running a model locally. Trialled during development for an exploratory anomaly pass, found too compute-heavy on the build machine, and removed. Written up as a finding in the past tense, not a component. See [Tech Stack](Tech-Stack).

**Oracle approver.** The simulated Gate-2 approver used in the longitudinal replay, so the learning loop can run end to end. It is not a person, and the write-up says so.

**Projection.** An estimate made up front from the firm's cost assumptions. Labelled as such everywhere, and never used to decide whether a fix worked.

**Provenance guard.** The control that records which drive each analysis came from and refuses to measure an outcome against a snapshot that is simulated or of unknown origin. It fails closed.

**RAG (retrieval-augmented generation).** The method of grounding a model's answer in retrieved evidence. Here, past resolutions.

**Remediation executor.** The step that cleans the data after approval. It maps free-text statuses to a small controlled vocabulary and writes cleaned copies.

**Repetition.** A bottleneck type. A duplicate-work stage appears.

**Resolution store.** The searchable index of past fixes. The knowledge the diagnosis step retrieves against. Only validated, effective fixes enter it.

**Rework.** A bottleneck type. Work loops back to an earlier stage.

**Scrub.** The step that replaces personal data with placeholders before any payload leaves the machine.

**Simulator.** The second system: a simulated firm that advances a day at a time, renders a messy drive, and lets approved actions change what happens next. Not part of the product. See [Live Simulator](Live-Simulator).

**SME.** A small or medium enterprise. The kind of firm this project serves.

**Synthetic data.** Made-up data, built on purpose for testing. All data in this repo is synthetic.

**Today queue.** The dashboard's primary view: the ranked list of work that needs attention now. The charts and bottleneck cards are supporting evidence for it.

**Trusted knowledge.** A fix that reached `validated`, showed a measured improvement against a later analysis, and had a human confirm the reading. Only these become retrievable advice.

**Validated.** The lifecycle status for an intervention whose outcome was measured and confirmed. Distinct from `approved`, which is only a decision.

**Wired finding type.** A finding type the simulator models a worker response for — the six case rules. Anything else is reported as `unwired` rather than silently ignored, so coverage can be stated honestly.
