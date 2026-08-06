# SME Bottleneck Pipeline — Project Wiki

This wiki explains an MSc research project for the COM748 module at Ulster University (MSc Artificial Intelligence).

**Project title:** Agentic Workflow Optimisation for SME Operations — An End-to-End Pipeline for Bottleneck Detection, RAG-Based Diagnosis and Human-in-the-Loop Approval.

**Student:** Emmett Murray (B00810618)
**Supervisor:** Dr Jose Santos
**Case study partner:** Foyle, an educational-tourism SME.

## What the project does

The system reads messy spreadsheet data from a small business. It finds where work gets stuck. It explains why, using past fixes as evidence. Then it turns each finding into a piece of work someone can pick up today. A person approves every step that changes data or advises staff.

It runs on one laptop. It uses no live company credentials. All data in this repo is synthetic.

## One-line summary

A lightweight, locally-run pipeline that ingests messy SME data, detects operational bottlenecks, diagnoses them with grounded suggestions, and carries out approved fixes — with a human approving every consequential action.

## The loop the worker sees

Charts and bottleneck summaries are supporting evidence. The product is the queue of work that comes out of them.

```mermaid
flowchart LR
    E["Evidence<br/>what the data shows"] --> C["Constraint<br/>where work sticks"]
    C --> K["Affected cases<br/>who is waiting"]
    K --> A["Recommended action"]
    A --> O["Owner<br/>+ due date"]
    O --> D["Completion"]
    D --> M["Measured outcome"]
    M -. "only a real improvement<br/>becomes trusted advice" .-> E

    classDef ev fill:#fde68a,stroke:#b45309,color:#111827
    classDef work fill:#bfdbfe,stroke:#1d4ed8,color:#111827
    classDef out fill:#bbf7d0,stroke:#15803d,color:#111827
    class E,C,K ev
    class A,O,D work
    class M out
```

See [Action Layer](Action-Layer) for how a finding becomes a task with an owner.

## The pipeline at a glance

```mermaid
flowchart LR
    F["Messy<br/>spreadsheets"] --> G1{{"GATE 1<br/>Mapping Review"}}
    G1 --> L[("Clean<br/>event log")]
    L --> D["Detect"]
    D --> R["Diagnose<br/>with RAG"]
    R --> Q["Action queue"]
    Q --> G2{{"GATE 2<br/>Approve"}}
    G2 --> X["Execute<br/>or track"]

    classDef inp fill:#fde68a,stroke:#b45309,color:#111827
    classDef gate fill:#fecaca,stroke:#b91c1c,color:#111827
    classDef store fill:#bbf7d0,stroke:#15803d,color:#111827
    classDef core fill:#c7d2fe,stroke:#4338ca,color:#111827
    class F inp
    class G1,G2 gate
    class L store
    class D,R,Q,X core
```

Each box is a page below.

## How to read this wiki

Use the sidebar. The pages follow the project from start to finish:

1. Start with the [Introduction](Introduction) for the problem and the aim.
2. [Background](Background) covers the SME context and the research gap.
3. [System Architecture](System-Architecture) is the core design, and [Data Strategy](Data-Strategy) covers the data it runs on.
4. The pipeline pages walk through each stage: [Ingestion and Mapping](Ingestion-and-Mapping), [Bottleneck Detection](Bottleneck-Detection), [RAG Diagnosis](RAG-Diagnosis), [Action Layer](Action-Layer), [Human-in-the-Loop](Human-in-the-Loop), and [Remediation](Remediation).
5. [Live Simulator](Live-Simulator) is the *second* system — a simulated firm that reacts to what the product recommends. It is what makes an approved fix measurable.
6. [Evaluation](Evaluation) reports the results.
7. [Privacy and Ethics](Privacy-and-Ethics), [Tech Stack](Tech-Stack), and [How to Run](How-to-Run) cover the practical side.
8. The [Glossary](Glossary) defines the terms.

## Status

This is a work in progress. It is a dissertation build, not a production product. See [Scope and Status](Scope-and-Status) for what is in scope and what is not.
