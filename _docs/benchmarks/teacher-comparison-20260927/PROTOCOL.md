# Gemma / Codex semantic saturation teacher pilot

Date: 2026-09-27 JST. This is an offline comparison requested by the user.
No runtime backend, production model artifact, or conversation log is changed.

## Question

With the existing Japanese character n-gram + ridge student held fixed, does
changing the labeling teacher improve readiness classification, and how does it
change labeling, fitting, and resident prediction time?

The target follows `server/tomoko/semantic.py::saturation_prompt`: whether the
listener may START responding now, including a useful brief response during an
unfinished utterance. It is not a claim that acoustic speech has ended.

## Data and privacy

Only newly authored artificial Japanese text is sent to either teacher.
There are 160 training and 80 evaluation examples, grouped by situation. Related
fragments remain in the same split. Inputs have no real conversations or JDD.
Teachers see only neutral IDs and texts, never split, category, intended result,
future continuation, or another teacher's results.

References are expectations written by a separate Codex agent before teacher
collection, reviewed for ambiguous elliptical questions, and then frozen.
They are **not human ground truth**. Shared model-family/style bias can favor
Codex. Ambiguous items remain available for inspection but are excluded from
binary reference scoring. A favorable result is a pilot signal, not proof of
superior real-conversation turn-taking.

## Fixed comparison

- Same Japanese saturation definition and few-shot examples for both teachers.
- The original single `SATURATION=` output is adapted to the same JSON batch
  of 20 independent inputs for both teachers, solely to collect offline labels.
- Both see the same seeded order, split into train and evaluation batches.
- Codex uses the user's configured `gpt-6-astra`, reasoning `medium`, through
  an isolated CLI invocation with user configuration disabled and no tools used.
  Codex still has agent-system overhead, unlike the bare Gemma HTTP endpoint.
- Gemma uses the existing local fused Gemma 4 26B A4B 4-bit weights, served by
  MLX on loopback. This is not a fresh untouched base-model checkpoint or the
  historical DFlash serving setup. Record the exact paths and hashes.
- Temperature 0 and thinking disabled for Gemma; no claim of matched internal
  reasoning/computational budget between model families.
- Strict ID and score validation; no heuristic fallback or silent correction.
- Primary student: existing hash-ridge, 2048 features, character 1–4 grams,
  ridge lambda 1.0, 160 teacher-only training labels. No manual anchors added.
- All `is_final` features are held True in train and evaluation to match the
  current runtime's finalish scoring and avoid revealing a synthetic endpoint.
- Readiness threshold fixed at 0.75 before observing outputs. No tuning on eval.
- Existing public-synthetic student is a baseline with a different training set,
  not a matched teacher-replacement condition.

## Measurements

Teacher agreement and mean absolute score difference measure consistency, not
correctness. Against predeclared non-ambiguous references report class counts,
balanced accuracy, false-start and false-wait counts/rates. Evaluate teachers
and their students separately; retain every score for review.

Teacher time includes collection overhead per batch; amortized time per item is
not single-call response latency. Record wall time for all labels (including eval
labels), training batches separately where possible, usage, failures, warmup and
startup separately. Fit timings use alternating teacher order for three repeats.
Resident student predictions are warmed up and measured separately from model
load or CLI startup. Simultaneously collecting independent local/cloud teachers
is not a same-machine model speed benchmark.

Training is the existing normal-equation solve; it has no epoch-based stopping
criterion. Better labels need not reduce solve time, but may reduce later rounds
of data correction. That latter effect is not measured by this experiment.

Before collecting either teacher, the secondary configuration is also fixed to
8192 features / lambda 0.01, matching the existing public runtime model's config.
It uses exactly the same 160 teacher-only labels and is not selected by eval score.

## Format-only amendment after first collection attempt

The first Gemma batch returned 20 numeric scores but misspelled one long neutral
hex ID (a missing character). Strict validation rejected the entire batch; no
scores were repaired or accepted. The original Codex long-ID attempt is retained.
For the paired run, both teachers instead receive short batch-local IDs `1..20`,
mapped explicitly back to the frozen corpus IDs after exact validation. Text,
rubric, order, reference, student settings and threshold do not change.
The paired run lives under `paired-short-ids/`; preserve all earlier attempts and
report their overhead separately. This tests practical labeling throughput as
well as semantic scores without treating an ID-copy error as a valid label.
