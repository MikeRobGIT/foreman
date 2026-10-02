# Improvement Backlog

Queue for `/foreman`. It picks the **topmost `status: ready` row**; `risk: high` rows run only when named
(`/foreman IB-2`). One row = one PR-sized unit.

- **status:** `ready` (pickable) · `deferred` (opt-in only) · `blocked` (waiting on something) · `done (#PR)`
- **acceptance:** the verifiable success criterion the pass gates on before opening the PR
- Keep each row on ONE line, and write a literal pipe inside a cell as `\|`.

| id | title | risk | status | acceptance | source |
|----|-------|------|--------|------------|--------|
| IB-1 | Example: add a unit test for the date parser's leap-day branch | low | ready | A new test fails on the old code and passes on the fix; the gate is green | setup example, replace me |
