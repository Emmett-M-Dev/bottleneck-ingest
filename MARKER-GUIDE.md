# A guide to running this system

**Emmett Murray (B00810618) — COM748 MSc Research Project**

This guide assumes no knowledge of the code. It takes about 15 minutes to work
through, and everything you need to click is described.

---

## 1. What this is, in one paragraph

Most small businesses run their operations on spreadsheets. Work gets stuck, and
nobody notices until a client complains. This system reads a small firm's messy
spreadsheets, works out which jobs need attention today, explains the evidence
behind each one, and asks a human to approve any action before anything happens.
It then measures whether the action actually worked.

The loop it implements is:

> evidence → what's stuck → which jobs → recommended action → owner → due date
> → completion → **measured outcome**

That last step is the part most tools skip.

---

## 2. Starting it

You need two terminals. On Windows, from the project folder.

**Terminal 1 — the analysis engine's API:**

```
cd ../hitl-react/api
.venv/Scripts/python.exe -m uvicorn main:app --port 8010
```

**Terminal 2 — the dashboard:**

```
cd ../hitl-react
npm run dev
```

Then open the address the second terminal prints — usually
**http://localhost:5173**.

> If the page loads but shows no data, the first terminal is not running. The
> port must be **8010**; the dashboard is configured to look there.

---

## 3. Pick the demonstration business

Click the business name in the top-left and choose **Northstar Advisory**.

There are three fictional SMEs in the system — an educational-tourism agency, a
joinery firm, and this consultancy. They exist to show the same analysis engine
works on very different businesses. **Please use Northstar Advisory for the
demonstration**; it is the one with the live simulator attached.

There are three tabs: **Today**, **Mapping Review**, and **Demo**.

---

## 4. The demonstration, step by step

### Step 1 — Today: what needs attention

This is the product. Each card is one thing a worker should do.

**Expand the top card.** Look for:

- **The evidence** — actual spreadsheet row references. Every recommendation
  traces back to rows in the firm's own files.
- **The affected jobs**, listed by reference.
- **The recommended action**, in plain English, with an owner and a due date.
- **"Diagnosis grounded in N past resolutions"** on a card about a stuck stage.
  Expand it. The system searched a store of past fixes and shows which ones it
  drew on, with a similarity score. This is the retrieval-augmented part — it is
  not the model inventing advice.

**Click "See exactly what was sent to the AI."** This opens the literal payload.
Names and identifiers are replaced with placeholders before anything leaves the
machine. This is the privacy control, and you can inspect it rather than take it
on trust.

**Notice the two kinds of card.** Most say approving changes no files — the
action is work for a person, tracked. A data-cleaning card says approving *does*
change files, and even then it writes cleaned copies and never touches the
originals.

### Step 2 — Mapping Review: the first human gate

Before the system reads a business's files at all, it has to work out what each
spreadsheet column means. It proposes a mapping; a human confirms or corrects it.

Look at the per-column confidence, and the report of what is messy about the
drive — duplicate files, overlapping copies, a sheet with no usable structure.
Nothing is ingested until a human approves this screen.

### Step 3 — Demo: watch the business run

This is the part that shows the loop closing.

**Click "Reset to day 0", then "▶ Run demo".**

A simulated version of the business now runs forward, one day every few seconds:

| What you'll see | What it means |
|---|---|
| Emails arriving in the left sidebar | Clients contacting the firm — a new enquiry, a query, a payment |
| A message striking through | The firm's staff have typed it into the spreadsheet |
| Rows flashing green in the grid on the right | The actual spreadsheet changing |
| Inconsistent spellings — `lead`, `PROPOSAL`, `Won ` | Deliberate. Real spreadsheets look like this, and the system has to cope |
| Two different column layouts across the tabs | Also deliberate — sales and delivery keep separate sheets with different headings |
| **The day counter pausing for about 50 seconds at day 7** | **Not a hang.** The system is re-reading the whole drive and re-running its analysis. The clock deliberately stops so it isn't showing you a day count that's ahead of the data |
| The figures at the top changing after that pause | The analysis has updated |

### Step 4 — the point of the whole thing

1. Go back to **Today** and **approve** an item — give it an owner and a due date.
2. Return to **Demo** and keep running.

The approved action changes what the simulated staff do next, and the jobs behind
that finding start clearing. The system can then measure whether the action
worked, rather than assuming that approving it was enough.

---

## 5. What is real and what is simulated

Stated plainly, because it matters for assessment.

**Real:** the analysis engine, the detection of stuck work, the retrieval of past
fixes, the privacy scrubbing, the approval gates, the outcome measurement
machinery, and the spreadsheet-reading. All of it runs locally on this machine.

**Synthetic:** the businesses and their data. All three firms, their clients,
staff and money are invented. This is deliberate — it means the correct answers
are known in advance, so the detection can be scored against them.

**Simulated, and authored:** in the Demo tab, how the world responds to an
approved action is written into a configuration file, and some approved actions
deliberately fail. What the demonstration shows is that an improvement can now be
*observed and measured*. It is **not** evidence that these actions would help a
real firm — that would need a live deployment, which is outside this project's
scope.

---

## 6. Things worth knowing before you ask

- **The dashboard warns you when it is showing simulated data.** An amber strip
  appears on Today naming the drive the analysis came from.
- **Approving is blocked when the system cannot prove where its data came from.**
  It refuses rather than guessing — for the other two businesses, this means the
  approve button will refuse until their data is re-read.
- **Please run only one dashboard at a time.** Two would write to the same file.
- **The AI is used in exactly three places:** proposing the column mappings,
  diagnosing a stuck stage, and suggesting a status clean-up. Everything else —
  the detection, the ranking, the cost figures, the outcome verdict — is ordinary
  deterministic code. That is a design decision, not a shortcut: a worker can
  argue with arithmetic.

---

## 7. If you only have five minutes

1. **Today** → expand the top card → point at the evidence and the "grounded in
   N past resolutions" block.
2. Click **"See exactly what was sent to the AI"** → the placeholders.
3. **Demo** → Reset → Run → watch one email arrive, get typed into a
   spreadsheet, and the row flash.

That is the whole idea: messy spreadsheets in, a specific thing to do today out,
with the evidence attached and a human in control.
