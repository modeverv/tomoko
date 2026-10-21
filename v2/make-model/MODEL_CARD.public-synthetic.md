# Public Synthetic Semantic Saturation Model Card

## Model

Hash-ridge character n-gram regression model for Japanese conversational
semantic saturation.

The model returns a float in `0.0..1.0`:

- Higher values mean Tomoko may start responding now.
- Lower values mean the user utterance likely continues, or responding now may
  feel like an interruption.

## Training Line

```text
synthetic Japanese utterance fragments
  -> Gemma 4 26B teacher labels
  -> handcrafted synthetic anchors
  -> hash-ridge saturation scorer
  -> JSON artifact
```

No internet dialogue corpus is used in this public-synthetic training line.

## Intended Use

- Low-latency turn-taking support for Tomoko.
- Partial/final STT gating.
- Candidate early-start scoring.

## Out of Scope

- General language understanding.
- Safety classification.
- Factuality checking.
- User identity or emotion inference.

## Known Limitations

- It learns the teacher/anchor policy, not ground truth.
- It is character-ngram based and can be brittle for unseen phrasing.
- It should be combined with final STT, VAP/MaAI signals, motivation, and
  reconcile logic rather than used as the sole speech trigger.
