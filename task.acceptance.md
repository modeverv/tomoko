# ACCEPTANCE.md

## safe(T) Score

- C: 0.8
- O: 0.8
- I: 0.2
- safe = C x O x (1 - I): 0.512

## User Instruction

participation:called は確認できなかった。ambient_logsには
「ともこ、聞こえますか？」と何回か発話したが
「ともく」や「聞こえますか？」記録されているようである。
モデルの精度だろうか？それともなにか設定があるのだろうか？
この辺りを調べて修正してほしい。

## Completion Criteria

- [ ] Worker changed only files inside ownership paths.
- [ ] Relevant verification commands were run or explicitly documented as not run.
- [ ] Final state is recorded in summary.md.

## Verify Commands

```bash
# Add verification commands here — they must exit 0 for the job to succeed.
# e.g.: python -m pytest -q
# e.g.: python -m py_compile path/to/file.py
```

## Ownership

```yaml
ownership:
  mode: write
  paths:
    - .
```

## Human Gate

- safe < 0.3 requires human_gate before execution.
- Out-of-scope changes require human review.
- Worker uncertainty requires human review.
