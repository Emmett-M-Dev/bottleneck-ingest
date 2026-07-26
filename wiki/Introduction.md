# Introduction

## The problem

Small and medium businesses run on messy data. Staff track work in spreadsheets. Column names drift. People type the same status ten different ways. Work stalls, and no one can see where or why.

Large firms fix this with data teams and expensive tools. Small firms cannot. They lack the budget, the staff, and the clean data these tools need.

The numbers show the gap. OECD data puts AI use at about 40% for large firms and about 12% for small firms. The gap comes from resource limits, not from a lack of good ideas. Small firms face unclear returns, no AI-ready data, and skills gaps.

## The aim

This project builds a system that a small firm can run without a specialist team. It must be light. It must run on one laptop. It must work with the messy data the firm already has.

The system does four things:

1. **Reads** messy spreadsheets and maps them to one clean schema.
2. **Detects** where work gets stuck.
3. **Diagnoses** the cause and suggests a fix, backed by past fixes.
4. **Acts** on approved fixes and cleans the data.

A person checks the work at two points. The system never changes data or advises staff on its own.

## The contribution

The main claim is **generalisability**. The same core runs for two very different firms with no new detection code. To add a second firm, you add a config block and one approved mapping. Nothing in the core changes.

This matters because it shows the approach can spread across small firms. It is not tuned to one business. The [Evaluation](Evaluation) page shows the evidence.

## Why "agentic"

The system uses AI agents for two precise jobs: reading messy files and diagnosing bottlenecks. It uses a graph to run the steps in order. Each agent proposes; a human disposes. The design keeps the human in charge of every choice that matters.
