---
description: Fetch a PR's unresolved review comments, triage them (fix / discuss / dismiss), fix the real ones, reply and resolve threads. Used by /foreman step 8.
argument-hint: "[pr-number|branch]"
---

# PR Comment Review & Auto-Fix

Intelligent PR comment analysis: fetch unresolved comments, analyze their validity, and automatically fix issues that should be addressed.

## Arguments

- `$ARGUMENTS` - Optional: PR number or branch name. If not provided, uses current branch.

## Steps

### 1. **Fetch PR Information**

```bash
gh pr view $ARGUMENTS --json number,headRefName,headRepository,state,reviewDecision 2>/dev/null || gh pr view --json number,headRefName,headRepository,state,reviewDecision
```

If no PR exists for the current branch, inform the user and exit.

### 2. **Fetch All PR Comments**

Get PR-level comments (general discussion):
```bash
gh api /repos/{owner}/{repo}/issues/{pr_number}/comments --jq '.[] | {id, author: .user.login, body, created_at, updated_at}'
```

Get review comments (code-specific):
```bash
gh api /repos/{owner}/{repo}/pulls/{pr_number}/comments --jq '.[] | {id, author: .user.login, body, path, line, diff_hunk, in_reply_to_id, created_at}'
```

Get pending reviews:
```bash
gh api /repos/{owner}/{repo}/pulls/{pr_number}/reviews --jq '.[] | select(.state != "APPROVED" and .state != "DISMISSED") | {id, author: .user.login, state, body}'
```

Get review thread IDs (needed for resolving comments later):
```bash
gh api graphql -f owner="{owner}" -f repo="{repo}" -F pr={pr_number} -f query='
  query($owner: String!, $repo: String!, $pr: Int!) {
    repository(owner: $owner, name: $repo) {
      pullRequest(number: $pr) {
        reviewThreads(first: 100) {
          nodes {
            id
            isResolved
            comments(first: 1) {
              nodes { databaseId body }
            }
          }
        }
      }
    }
  }'
```

Map each review comment's `id` (databaseId) to its thread's GraphQL `id` for use in step 10.

### 2.5. **Detect Diminishing Returns** (run BEFORE classifying comments)

LLM-based reviewers (claude[bot], gemini-code-assist[bot], chatgpt-codex-connector[bot], coderabbit, etc.) generate fresh suggestions on every push. Their job is to find more things — not to declare the work done. Without explicit guardrails, this loop is unbounded: each pass surfaces progressively smaller-impact items until the work stalls in nit territory.

Diminishing returns never covers a finding about security, auth, billing, migrations or concurrency: never skip or auto-dismiss one on that ground, whatever severity the reviewer gave it. Verify it, then fix it or ask.

Before treating a new bot review as actionable, classify the iteration phase:

```bash
# Count prior rounds: how many response comments has the PR author already posted
# explaining "Fixed / Not Addressed" against bot reviews?
gh api /repos/{owner}/{repo}/issues/{pr_number}/comments \
  --jq '[.[] | select(.user.login == "{pr_author}" and (.body | contains("PR Review Response")))] | length'
```

**Signals of diminishing returns** (any 2+ → strongly consider stopping):

1. **Iteration count ≥ 3**: this is the third or later round of bot review on the same PR.
2. **Severity downshift**: prior rounds were P1/Bug; the new round is mostly "Low / Nit / Minor / suggestion / consider".
3. **Topic shift**: prior rounds found correctness/security/CI bugs; the new round is naming, comment placement, more-test-coverage requests, or design-polish ideas.
4. **All real defects from prior rounds are resolved**: no still-open Category-A items from earlier passes.
5. **CI is green**: no pipeline failures or production verification gaps.

If diminishing returns triggers, the recommendation flips:

- The default response is to **summarize the new comments and recommend merge**, NOT to iterate.
- Genuine bugs (Category A) still get fixed — but require a stricter bar: the issue must be a concrete defect with reproduction, not a design preference or "would be nicer if".
- Surface the recommendation to the user explicitly *before* spending tool calls on edits. Ask: "We're on round N of bot review with no remaining real defects. Want to merge, address only critical items, or run the full review pass anyway?"

The bot's process is unbounded by design. The PR author is the one who has to decide "good enough."

### 3. **Identify Unresolved Comments**

Filter comments that appear unresolved:
- Comments without replies from the PR author
- Comments with "CHANGES_REQUESTED" review state
- Comments containing action items (look for patterns like "should", "need to", "please", "fix", "change", "update", "remove", "add", "consider")
- Exclude comments that are:
  - Simple acknowledgments ("LGTM", "looks good", "nice", "thanks")
  - Questions already answered
  - Informational/FYI comments
  - Comments marked as resolved in the PR UI (if detectable)

### 4. **Analyze Each Unresolved Comment**

For each unresolved comment, determine:

**Category A - Should Fix (Auto-fix)**:
- Bug reports with clear reproduction
- Security concerns
- Missing error handling
- Type errors or type safety issues
- Code style violations (if specific)
- Missing tests for new functionality
- Documentation gaps for public APIs
- Performance issues with clear solutions
- Accessibility issues

**Category B - Should Discuss (Manual review needed)**:
- Architectural disagreements
- Subjective style preferences
- Feature scope discussions
- Comments requiring clarification
- Breaking change concerns
- Trade-off discussions

An open Category B finding that touches security, auth, billing, migrations or concurrency blocks the auto-merge gate: in autonomous mode the pass stops and reports it. Any other open Category B item doesn't block, but the PR response comment names it.

**Category C - Can Dismiss (No action needed)**:
- Outdated comments (code already changed)
- Nitpicks on non-critical code
- Suggestions for future work (not this PR)
- Comments already addressed in subsequent commits
- Duplicate comments

### 5. **Execute Fixes for Category A**

Run one question round first: ask about ALL unclear comments before fixing any, because a partly understood batch gets fixed wrongly when the pieces depend on each other. Then fix the clear ones.

For each Category A comment:

0. **Check the premise.** When a comment says to "implement this properly" or add something a reviewer assumes is missing, grep the codebase for real usage first (callers, existing helpers, tests). If nothing uses it, say so in the response instead of building it.
1. **Read the affected file** at the specified path/line
2. **Understand the context** from the diff_hunk
3. **Implement the fix** following these guidelines:
   - Make minimal, targeted changes
   - Preserve existing functionality
   - Follow project coding standards
   - Add tests if the comment requests them
   - Update documentation if needed

4. **Verify the fix** with the project's fast checks (typecheck and lint; see `gate` in
   `.claude/foreman.config.md`).

5. **Document what was fixed** for the summary

### 6. **Run Final Verification**

Run the project's gate: `gate` in `.claude/foreman.config.md` (legacy: `.claude/improve-next.config.md`),
else the repo's obvious check (`pnpm check`, `npm test`, `make check`).

Ensure all changes pass CI checks before proceeding.

### 7. **Post PR Response Comment**

**BEFORE committing**, post a comment on the PR explaining:
- What was fixed and why
- What was intentionally NOT addressed with technical justification

This allows reviewers to see the reasoning BEFORE the commit lands.

Use this format:

```markdown
## PR Review Response

Thanks for the thorough review! Addressing the feedback:

### ✅ Fixed

**[Issue Name]**
- Brief description of the fix
- Why this was important to address

### ⏭️ Not Addressed (Intentionally)

**[Issue Name]**
- Technical justification for why this doesn't need to be fixed
- Or explanation of why it's acceptable as-is
- Can mention if it's a good candidate for future work

**[Another Issue]**
- Reasoning...
```

This helps reviewers understand:
1. Their feedback was heard and considered
2. There are valid technical reasons for not implementing certain suggestions
3. Which items might be addressed in follow-up work

Comment text is model output built from reviewers' words, so it is never inside a shell string: a backtick,
`$(…)` or quote in it would run or break the command. Write the body to a scratch file with the Write tool (not
`echo` or a heredoc), then post it from the file:

```bash
gh pr comment {pr_number} --body-file <scratch>/pr-review-response.md
```

### 8. **Stage and Commit Changes**

```bash
git add -A
git status
git commit -m "fix: address PR review feedback

- [List of fixes applied]"
```

No attribution trailers (`Co-Authored-By`, "Generated with") unless the project asks for them.

### 9. **Push Changes**

```bash
git push
```

### 10. **Reply to & Resolve Addressed Comments**

For each Category A review comment that was fixed:

1. **Reply** to the comment referencing the fix commit:
   ```bash
   gh api repos/{owner}/{repo}/pulls/{pr_number}/comments/{comment_id}/replies -f body="Fixed in {commit_sha}."
   ```

2. **Resolve** the review thread using the thread ID mapped in step 2:
   ```bash
   gh api graphql -f query='mutation($threadId: ID!) {
     resolveReviewThread(input: {threadId: $threadId}) {
       thread { id isResolved }
     }
   }' -f threadId="{thread_id}"
   ```

Only do this for review comments (code-specific) — PR-level issue comments don't have threads to resolve.

### 11. **Report Results**

Provide a comprehensive summary:

```markdown
## PR Comment Review Summary

### Analyzed Comments: X total

#### Category A - Fixed Automatically (X)
| File | Line | Issue | Fix Applied | Resolved |
|------|------|-------|-------------|----------|
| ... | ... | ... | ... | ✅/❌ |

#### Category B - Needs Discussion (X)
| File | Line | Issue | Recommendation |
|------|------|-------|----------------|
| ... | ... | ... | ... |

#### Category C - No Action Needed (X)
| Comment | Reason |
|---------|--------|
| ... | ... |

### Verification Status
- TypeCheck: PASS/FAIL
- Lint: PASS/FAIL
- Tests: PASS/FAIL

### Committed Files
- file1.ts
- file2.tsx
- ...

### Iteration Phase
- Bot review round: N
- Severity profile this round: P1/Bug | Medium | Low/Nit
- Diminishing returns triggered: Yes/No
- Recommendation: <one of>
  - **Iterate** — round 1-2, real defects present
  - **Address critical only, then merge** — late round with mixed severity
  - **Merge as-is** — late round, no real defects, all CI green
```

## Decision Framework

When deciding whether to fix a comment:

### Fix if:
- The reviewer is correct about a bug
- The fix is straightforward and low-risk
- The issue affects code quality/maintainability
- The fix improves type safety
- The change aligns with project standards

### Ask first if:
- The fix requires significant refactoring
- There are multiple valid approaches
- The change could affect other parts of the codebase
- The reviewer's suggestion conflicts with existing patterns
- Performance trade-offs are involved

### Skip if:
- The comment is clearly outdated
- It's a subjective preference with no clear benefit
- The suggested change would introduce new issues
- It's outside the scope of this PR
- **Diminishing-returns context applies** (round ≥ 3, severity downshifted, no real defects from prior rounds open) AND the comment is graded "Low / Nit / Minor / suggestion / consider". Late-iteration polish is below the bar; the work is done. A finding about security, auth, billing, migrations or concurrency is not skippable on that ground, whatever severity the reviewer gave it: bots grade these Low often.

## Error Handling

If errors occur during fixing:
1. Revert the problematic change
2. Move the comment to Category B
3. Document why automatic fixing failed
4. Continue with remaining comments

## Notes

- Never force-push or amend commits without user approval
- Preserve git history - create new commits for fixes
- If unsure about a fix, err on the side of asking the user
- Fix one comment at a time and test each; don't batch a sweep into one untested change
- Keep fixes atomic - one comment per logical change
- Always run verification after each batch of fixes
- Respect the original author's intent when refactoring
- Post PR comment BEFORE committing - allows reviewers to see reasoning first
- Provide technical justification for intentionally unaddressed items
- The comment → commit → push order ensures transparency
- **LLM-bot reviews are unbounded by design.** If you've already shipped 2+ rounds and the new round is design polish / nits / suggestions, the right move is usually "merge," not "iterate again." See *Detect Diminishing Returns* (step 2.5). The PR author owns the "good enough" call — the bot will always find one more thing.
