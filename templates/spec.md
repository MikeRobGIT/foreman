# ⟦FILL: the batch's row ids joined by " + "⟧ — design

Batch `⟦FILL: batch name⟧` on branch `⟦FILL: branch⟧`. Written in the spec phase: no code for it exists yet. A section with nothing in it reads "None — <reason>"; never delete a section.

## Rows

⟦FILL: for each row, its id, title and acceptance, quoted from the backlog⟧

## Current behaviour

⟦FILL: what happens today, with `path:line` evidence for every claim⟧

## Change

⟦FILL: what will be different, per row⟧

## Out of scope

⟦FILL: what this change deliberately does not do, including nearby things a reviewer might expect; extra work outside this list and the Change is scope creep; or "None — <reason>"⟧

## Callers and writers checked

⟦FILL: for each symbol, column, status or file the change touches: every site `git grep` found and why each one is or isn't affected; for each write, the client or credentials that perform it, the roles that reach it, and why row-level security or permissions allow it⟧

## Rules that apply

⟦FILL: each rules-file entry that applies, quoted with file:line, and how the change honours it⟧

## Tests

⟦FILL: the failing tests to write first, each with the behaviour it pins; mutants the tests must kill, where useful⟧

Seams: ⟦FILL: where the tests attach, the highest seam that still pins the behaviour (ideally one), and why a lower one isn't needed⟧

Expected values in a test come from an independent source (the row's acceptance, a fixture, a hand-computed number), never from the code under test: a test that can't disagree with the code proves nothing.

Feedback loop (bug rows): ⟦FILL: the one command that goes red on the bug before any fix, and the output that shows it; "None — not a bug row"; if no seam can make it red, say that, because it is itself a finding⟧

## Review focus

⟦FILL: up to five inputs, states or orderings the design implies but no listed test exercises, one per bullet, each pinned to a test in Tests or written "uncovered — <why>"; or "None — <reason>"⟧

## Implementation outline

⟦FILL: the files in order and what changes in each, labelled `create`, `modify` or `reference`, inside the batch's owned files and its own entries in parallel_shared_files; list any other file the change needs on its own line as "- **Scope request:** `<file>` — <why>"⟧

## Surface

⟦FILL: app surface yes or no; if yes, the planned browser check's claims and its control on main⟧

## Risks

⟦FILL: what could go wrong, and how the change or the tests cover it⟧

Door: ⟦FILL: one-way (a migration, a destructive data change, a public API or a message already sent) or two-way (a revert undoes it)⟧

Blast radius: ⟦FILL: what else breaks, and for whom, if this is wrong in production⟧

## Open questions

⟦FILL: each product decision the code can't answer, one bullet each, labelled `- **Blocking:**` (the batch can't be built as specified without the answer) or `- **Non-blocking:**`; or "None — <reason>"; a question that is answered moves to Review responses⟧

## Review responses

None — round 1
