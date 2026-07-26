# Privacy and Ethics

The project builds ethics into the code, not just the write-up.

## A human approves every action

The system suggests. A person decides. This holds at both gates. No mapping is trusted and no fix runs without a human choice. This keeps accountability with the firm and fits the ACM Code of Ethics on human oversight.

## Zero raw personal data to the cloud

The system calls the Claude API for two jobs: mapping inference and diagnosis. Both calls could carry personal data from the spreadsheets. So both scrub the data first.

Every sample cell in the mapping payload passes through the scrub step. Every evidence excerpt in the diagnosis payload does too. The scrub replaces names and other personal data with placeholders. A test asserts that only placeholders reach the payload.

So no raw personal data leaves the machine. This is the implemented privacy control, not a promise.

## Local model for exploratory work

The anomaly pass runs on a local model through Ollama. It sees aggregate stats only. Its data never leaves the machine. This splits the work by privacy need: local inference for exploratory analysis, cloud plus scrub for the two precise tasks.

## Test environment only

All execution testing happens in a test environment. The system never runs against a live company system. The remediation executor writes cleaned copies and never touches the originals. See [Remediation](Remediation).

## Everything is logged

Every executed action is logged with a timestamp. Both gates write decision logs. This gives a full audit trail.

## Transparency

The system always shows its working. It shows the retrieved evidence behind each suggestion. The dashboard even shows the exact scrubbed payload the model saw. The user is never asked to trust a black box.

## Secret handling

The API key lives in a local environment file that is not committed to git. The key must be rotated at the Anthropic console before submission, because it was once pasted in a development chat. This is a known task on the checklist.

## Data consent

The system uses synthetic data. Real firm data is used only as a one-off, consented, supervisor-signed-off export. The system never pulls live data with credentials. See [Scope and Status](Scope-and-Status).
