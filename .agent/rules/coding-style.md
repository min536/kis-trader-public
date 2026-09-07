# coding style

- Make the smallest safe change.
- Do not refactor unrelated code.
- Keep function names explicit and readable.
- Add logs/flags when behavior changes so the effect can be verified later.
- Preserve backward compatibility for existing CLI tools where practical.
- Prefer flat, analysis-friendly schemas.
- Keep terminal output compact and interpretable.
- When adding diagnostics:
  - explain counts
  - explain bottlenecks
  - avoid guessing causes not present in current data