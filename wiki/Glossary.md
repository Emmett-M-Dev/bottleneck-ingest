# Glossary

Plain definitions for the terms used across this wiki.

**Adapter layer.** The thin, per-firm part of the system. It maps a firm's messy columns to the fixed schema. It is the only part that changes per firm.

**Anomaly pass.** An optional, advisory scan on a local model. It adds "AI-spotted" cards. It is not scored against ground truth.

**Bottleneck.** A stage in a workflow where work gets stuck. This project detects three types: delay, repetition, and rework.

**Canonical schema.** The one clean data shape for all firms. Every row becomes an `Event` with a case id, an activity, a timestamp, an actor, a status, and a source reference.

**ChromaDB.** The vector store that holds the resolution knowledge for search.

**Circularity guard.** The rule that keeps the data-injection code apart from the detection code. It stops the detector from just learning the injection rules.

**Delay.** A bottleneck type. Cases wait too long entering or leaving a stage.

**Dynamic detection.** The detector that scans every stage and reports zero to N findings, with no marker config.

**Event log.** The canonical data store. The clean, normalised record of what happened.

**Gate 1, Mapping Review.** The first human gate. A person confirms the schema mapping before the system trusts the data.

**Gate 2, Fixes.** The second human gate. A person approves, rejects, or edits each fix before it runs.

**Generalisability.** The main claim. The same core runs for different firms with no new detection code.

**Ground truth.** The known answer, planted on purpose in the synthetic data, used to score detection.

**Human-in-the-loop (HITL).** The design where a person approves the choices that matter. This system has two such gates.

**LangGraph.** The library that runs the pipeline steps in order and pauses at the gate.

**Learning loop.** The process where an approved fix is saved back into the store, so the system can retrieve it next time.

**Mapping-inference agent.** The agent that reads messy files and proposes how each column maps to the schema.

**Ollama.** The tool that runs a local model on the machine, used for the anomaly pass.

**RAG (retrieval-augmented generation).** The method of grounding a model's answer in retrieved evidence. Here, past resolutions.

**Remediation executor.** The step that cleans the data after approval. It maps free-text statuses to a small controlled vocabulary.

**Repetition.** A bottleneck type. A duplicate-work stage appears.

**Resolution store.** The searchable index of past fixes. The knowledge the diagnosis step retrieves against.

**Rework.** A bottleneck type. Work loops back to an earlier stage.

**Scrub.** The step that replaces personal data with placeholders before any payload leaves the machine.

**SME.** A small or medium enterprise. The kind of firm this project serves.

**Synthetic data.** Made-up data, built on purpose for testing. All data in this repo is synthetic.
