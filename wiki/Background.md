# Background

## Small firms and the AI gap

Most firms are small. They employ most workers. Yet they use AI far less than large firms do. The OECD reports a wide gap: about 40% of large firms use AI against about 12% of small firms.

The cause is not skill or ambition. It is resources. Small firms hit three walls:

- **Unclear returns.** They cannot risk a big spend on a tool that may not pay off.
- **No AI-ready data.** Their data lives in spreadsheets, not clean databases.
- **Skills gaps.** They have no data team to set up and run the tools.

A useful system for small firms must clear all three walls. It must be cheap, work with messy data, and run without experts.

## What a bottleneck is

A bottleneck is a stage in a workflow where work piles up. Cases wait too long. Or the same step repeats. Or work loops back to an earlier stage.

Take an educational-tourism firm. It books students onto courses, places them with host families, and arranges transport. If host-family checks lag, students wait. That stage is a bottleneck.

This project detects three bottleneck types:

- **Delay** — cases wait too long to enter or leave a stage.
- **Repetition** — a duplicate-work stage shows up.
- **Rework** — work loops back to an earlier stage.

## Why grounded diagnosis

Finding a bottleneck is not enough. Staff need to know why it happens and what to do. A plain language model can guess, but a guess is not evidence.

This project grounds each suggestion in past fixes. It searches a store of resolutions and shows the ones it used. The user sees the working, not just the answer. This is retrieval-augmented generation, or RAG. The [RAG Diagnosis](RAG-Diagnosis) page covers it.

## Why keep a human in the loop

The system suggests. A person decides. This keeps accountability with the firm, not the model. It fits the ACM Code of Ethics on human oversight.

The design has two approval gates, not one. A person approves the schema mapping before any data is trusted. A person approves each fix before it runs. The [Human-in-the-Loop](Human-in-the-Loop) page explains both.
