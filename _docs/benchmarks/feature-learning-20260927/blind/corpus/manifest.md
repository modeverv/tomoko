# Frozen blind synthetic evaluation corpus, 2026-09-27

This corpus was authored and labeled by a dedicated Codex data subagent before
reading any teacher/student predictions from either experiment or the new
training corpus. The subagent had authored the earlier experiment corpus, so
independence is procedural independence from predictions and the new training
examples, not independence of model family or prior task knowledge. These are
AI-authored expectations, **not human gold labels**.

## Task

Given only the Japanese text, may the conversation partner start a relevant
response now? A short acknowledgment to a completed report counts. A clear
elliptical question or indirect request can be ready even without a full
predicate or request verb. A fragment explicitly waiting for its main clause,
critical specification, or correction is expected to wait. The objective is
not acoustic end of speech or grammatical completeness alone.

No actual conversation, audio, preceding turns, final-STT status, timing, or
personal data was used. Where short text requires a previous turn, referent,
or prosody to determine the intended speech act, its reference is null.

## Contents

- `inputs.jsonl`: exactly `id`, `text`, `split`, `group_id`.
- `reference.jsonl`: exactly `id`, `ready` (boolean or null), `category`, `reason`.
- `validation.json`: structural counts, exact-overlap result, and frozen SHA256.

All 240 items are eval; there are 120 scenario groups of two variants each.
After the pre-collection quality audit there are 116 ready, 107 wait, and
17 ambiguous items. Hard binary metrics use 223 items; report balanced accuracy
alongside accuracy. Pair ranking uses 107 ready/wait pairs. The item-accuracy
group bootstrap includes 116 groups with at least one unambiguous item; keep
the two variants together when both remain evaluable.
Do not train on these reference labels or optimize thresholds using this set.

The input order is shuffled with seed 2026092702. IDs are opaque text hashes.
Teachers should receive only the text and ID, without category/group/reference
information. Reference labels are loaded by the offline evaluator but are not passed to
model fitting or prediction. They are used to score already computed predictions;
all model conditions are fixed before this evaluation.

The corpus spans conjunction completion, elliptical questions, indirect
requests and situation reports, negation and quotation scope, corrections and
lists, complete statements versus fragments, nested clauses, and colloquial
speech. Items were composed individually across different situations; they
were not created by replacing a noun in one sentence template. An elliptical
question may end in a topic particle; an ordinary declarative report may be
ready without any explicit appeal for a reply. Quoted words and negated
requests do not automatically determine the label of the full utterance.

## Isolation and validation

Only exact text overlap with the previous teacher-comparison corpus was
checked, by a local script; the overlap is zero. Its reference labels and
predictions were not read. The new training data and its predictions were not
inspected. The parent experiment should check new-train exact overlap before
reporting results. Do not silently discard difficult items based on model
outputs; document any independently justified annotation correction as a new
version and rerun the comparison on the same frozen inputs.

All rows pass schema and ID checks, texts and IDs are unique, all groups have
two items, and input/reference IDs correspond one to one. Hashes in
`validation.json` apply to the final JSONL files.

## Limits

These are relatively clean, deliberately constructed Japanese texts. They do
not reproduce real STT errors, dialect diversity, acoustic hesitation, or
long conversational history. Incomplete clauses often have characteristic
endings; diverse constructions reduce one-template shortcuts but do not remove
all statistical grammar cues. Contrasting pairs are correlated and are not
240 independent real conversations.

The same model family creates the references and may create some training
labels, so this is a test of generalization to separately composed synthetic
expectations, not an unbiased comparison against human judgments. Readiness
is a pragmatic policy: another reasonable annotator could disagree. Human
review and a separately collected natural-conversation evaluation are needed
before making a production-quality claim.

## Pre-collection reference-quality audit (version 2)

The initial reference had 116 ready, 116 wait, and eight ambiguous items. Before
teacher scoring, student fitting, or reading model outputs, the parent approved
a linguistic audit of topic endings and wh fragments. Nine wait annotations
were changed to null because the texts can also serve as elliptical questions.
No text, ID, split, group, category, or input order was changed. These are
conservative annotation corrections, not changes chosen from model failures.

`quality-review.json` records every affected ID, prior and current reference
hashes, old and new reasons, authorization, and revised metric denominators.
The historical initial counts and hashes are preserved there. The current
reference hash in `validation.json` supersedes the original reference hash.
Human validation has still not been performed.

### Audit context clarification

The initial blind corpus was authored without seeing new training examples.
After that freeze, the parent shared two short training fragments to explain
the general elliptical-question annotation pitfall. The reviewer still did not
read the training files or model outputs. The nine reference-only corrections
were based on that linguistic audit before new teacher scoring/student fitting;
this is not a claim of complete information isolation during annotation review.
