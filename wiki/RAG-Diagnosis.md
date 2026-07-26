# RAG Diagnosis

This is the reasoning step of the fixed pipeline core. It turns a raw detection into a plain explanation and a fix.

## Why RAG

A model can guess a cause. A guess is not evidence. Staff need a reason they can trust.

So the system grounds each diagnosis in past fixes. This is retrieval-augmented generation, or RAG. The system finds similar past resolutions, then asks the model to reason over them. The user sees which past fixes shaped the answer.

## The resolution store

The knowledge store holds past resolutions. Each entry says how a similar problem was fixed before. The store is a vector index, so the system can search it by meaning, not by exact words.

For each bottleneck, the system builds a short query from the stage and the pattern type. It retrieves the nearest past resolutions. It ranks them by similarity.

## The diagnosis call

The system sends the model a small payload:

- The bottleneck: its type, stage, metric, and how many cases it hits.
- The firm context: the domain and the stage order.
- The past resolutions it retrieved.

The model returns a structured result: a diagnosis, a root cause, a suggested fix with steps, a confidence score, and the ids of the resolutions it used. A structured call means the answer cannot come back malformed.

Every evidence excerpt passes through the scrub step first. No raw personal data reaches the model. See [Privacy and Ethics](Privacy-and-Ethics).

There is a template fallback. If there is no API key or the call fails, the system produces a plain diagnosis from the detection numbers and the top retrieved fix. The pipeline never stalls.

## The learning loop

The system learns from its own approved fixes.

When a human approves or edits a fix at Gate 2, the system saves that fix into the resolution store. The next diagnosis can then retrieve the firm's own approved fix. Over time, the store fills with fixes the firm has already trusted.

This is what makes the system dynamic. It gets better as the firm uses it. The [Evaluation](Evaluation) page shows the learned-fix retrieval rate climbing from zero to one over the replay run.

## The cost model

Each fix carries a cost estimate, tuned per firm. This helps the user weigh a fix before approving it. The user sees the likely cost, not just the suggestion.
