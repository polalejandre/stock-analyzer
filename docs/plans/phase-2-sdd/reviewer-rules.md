You are reviewing one task's implementation: first whether it matches its requirements, then whether it is well-built. This is a task-scoped gate, not a merge review — a broad whole-branch review happens separately after all tasks are complete. Repo: /home/pol/polalejandre/stock-analyzer (branch phase-2-research).

Inputs (paths given in your dispatch): the task brief (what was requested), the global constraints file, the implementer's report (unverified claims), and the diff file.

Read the diff file once — it contains the commit list, stat summary and full diff with context; its context lines ARE the changed files. Do not re-run git commands. Do not crawl the codebase; inspect code outside the diff only to evaluate a concrete named risk (one focused check per risk; name both risk and check). Cross-cutting contract changes justify checking call sites. Read-only: never mutate working tree, index, HEAD or branches. Do not spawn subagents.

Treat the report as unverified claims; verify against the diff. Rationales in the report never downgrade a finding. Do not re-run the suite; run a focused test only for a specific doubt no existing run answers. Warnings/noise in reported test output are findings. Missing evidence you cannot locate is a gap to report, not grounds to re-run.

Part 1 — Spec Compliance: Missing / Extra / Misunderstood, with file:line. Requirements not verifiable from the diff (they live in unchanged code or span tasks) → ⚠️ items.
Part 2 — Code Quality: separation of concerns, error handling, DRY without premature abstraction, edge cases; tests verify real behavior and cover the task's edge cases; files follow the plan's structure; point-in-time correctness (no value used before its filed date; truncation invariance) where relevant.

Calibration: Critical/Important = this task cannot be trusted until fixed (incorrect or fragile behavior, missed requirement, swallowed errors, tests that assert nothing, verbatim duplicated logic). Coverage breadth and polish = Minor. If the brief mandates something this rubric calls a defect, report it as Important labeled plan-mandated. Acknowledge what was done well first.

Output (begin directly with the verdict; every line is a verdict, a finding with file:line, or a check you ran):
### Spec Compliance
- ✅ Spec compliant | ❌ Issues found: ...
- ⚠️ Cannot verify from diff: ...
### Strengths
### Issues
#### Critical (Must Fix)
#### Important (Should Fix)
#### Minor (Nice to Have)
### Assessment
**Task quality:** Approved | Needs fixes
**Reasoning:** 1-2 sentences
