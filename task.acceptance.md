# ACCEPTANCE.md

## safe(T) Score

- C: 0.8
- O: 0.8
- I: 0.2
- safe = C x O x (1 - I): 0.512

## User Instruction

うまくいきました。
Phase 6b: 静止画切り替えと声のトーンに進む を対応して下さい。画像はプロジクトにasset-factory_for_workディレクトリを切り、その中でpythonで画像生成するか、可能ならあなた自身が画像生成機能持ってるならそれで対応して下さい。

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
