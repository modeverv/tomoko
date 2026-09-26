# Synthetic teacher comparison corpus, 2026-09-27

This is a small **exploratory synthetic benchmark**, hand-composed by a Codex
subagent for this experiment. The reference expectations were declared before
seeing either teacher's predictions. They are **not human-validated gold labels**.
They may favor Codex because Codex authored both the examples and expectations;
agreement with these expectations must not be reported as real-world accuracy
or independent evidence that Codex is a superior teacher.

## Task definition

Judge whether the listener may **start responding now**, consistent with
`server/tomoko/semantic.py::saturation_prompt`. This includes a short relevant
response to an intelligible completed report or an indirect request. It does
not mean the speaker has acoustically stopped, and it does not imply the
listener has every fact needed to fulfill the whole request. Pure grammatical
completion alone is not the target.

Inputs contain only one Japanese utterance fragment, with no conversation
history, audio, duration, or final-STT flag. Both teachers and students must get
the same available information. Do not infer a missing previous conversation.

## Files and isolation

- `inputs.jsonl`: `id`, `text`, `split`, `group_id` only. Teacher requests should
  provide text and an opaque ID, without split/group/category/reference fields.
- `reference.jsonl`: `id`, `ready` (boolean or null), `category`, `reason`.
  These predeclared expectations are for exploratory diagnostics only and
  must not be passed to either teacher or included in student training labels.
- `validation.json`: structural checks, counts, and SHA-256 hashes.

The 160 train items come from 80 situations; the 80 eval items come from 40
other situations. Each situation contains two contrasting variants. All
variants from one situation stay in a single split. There are no exact text
duplicates across or within splits. No exact example sentence from the current
runtime prompt is included. Input order is shuffled with seed 20260927, and
IDs use a text SHA-256 prefix instead of encoding the expectation.

| Split | Ready | Incomplete | Ambiguous | Total |
| --- | ---: | ---: | ---: | ---: |
| Train | 78 | 78 | 4 | 160 |
| Eval | 38 | 38 | 4 | 80 |

The eight `ready: null` acknowledgments or context-dependent fragments are
excluded from binary reference scoring. Teachers may label them; their scores
are diagnostic. Any binary metric must state its denominator (76 unambiguous
eval items, not 80). Continuous teacher scores are not compared against invented
numeric reference scores; this corpus declares no numeric reference score.

## Coverage and limitations

Cases cover missing predicates/arguments, mid-word prefixes, trailing
conjunctions, explicit questions after conjunctions, self-correction, unfinished
lists, indirect requests, completed reports, and ambiguous short replies.
Categories describe the contrast family; both ready and incomplete variants
can share a category. Human-written natural conversation was not used.

The examples are original synthetic household, hobby, travel, and ordinary
conversation scenarios created in this task, not copied from JDD or actual
user conversations. No runtime/private logs, database conversations, external
corpora, or teacher outputs were read for their creation.

Limitations: paired contrasts and fairly clean punctuation may make the task
easier than noisy streaming STT. Complete/incomplete word endings correlate
strongly with labels. The train and eval situations differ but retain shared
Japanese grammatical constructions. There are few ellipses, dialects,
recognition errors, multi-party cases, pragmatic ambiguities, or utterances
whose response readiness changes with previous turns. A real held-out,
human-reviewed evaluation is required before changing the runtime model.

Do not tune teacher prompts, score thresholds, hash dimensions, regularization,
or data selection using these eval results and then call the same eval set
held out. Teacher disagreement lists should be inspected without treating the
synthetic author expectation as automatically correct.

## Pre-teacher ambiguity audit

Before either teacher was called, the root agent pointed out that bare topic
endings such as `のは` can be ordinary elliptical questions. Twelve inputs
were rewritten to expose an unfinished clause or correction rather than
assuming that corpus truncation automatically means low readiness. This was
an agent review, not human validation. The final hashes are in
`validation.json`; use that frozen version for both teachers.
