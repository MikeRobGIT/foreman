# A verdict from someone else, required before an autonomous merge — design

An autonomous pass (`loop` / `--auto-merge`) merges when its own checks are green. Step 6 already runs an independent reviewer and the cross-model chain, and parallel W4.2b already has a SHA-bound verdict, but both are prose: nothing at the tool call needs a verdict on the SHA being merged. Steps 7, 8 and 10 always move the head after step 6, so the verdict a pass collected may be about code that no longer exists, and the session that wrote the code can merge without anyone else having looked at the final SHA. `merge_discipline` only checks that `--match-head-commit` is present.

This adds a receipt: a verifier who didn't write the code returns a verdict for one PR at one head SHA, a hook records it, and a second hook refuses an autonomous merge with no passing receipt for the SHA it names.

## Goals and non-goals

- An autonomous merge needs a PASS from a verifier, for the exact head SHA, written by the harness and not by the merging session.
- A new commit voids the verdict, because the receipt is keyed by SHA.
- Manual mode is unchanged: the human's approval at the SHA is the verdict.
- Non-goals: a security boundary (foreman's hooks guard against mistakes; a session that prompts a puppet subagent to say PASS can still get one), a run ledger (declined in the hooks spec; a receipt is a gate artifact like the gate receipt), choosing the verifier's model.

## The receipt

`<state dir>/verdict/<pr>-<sha>`, JSON `{verdict: "PASS"|"FAIL", pr, sha, time}`, written into every directory of `repo.dirs` as gate receipts are. `<sha>` is the full 40 characters. Receipts are pruned with the gate receipts (same 14-day TTL).

## The verifier

Foreman dispatches it, not superpowers' reviewer skill, because that skill's prompt can't carry a tag.

- The prompt's first line is `foreman-verifier: <pr> <sha>`.
- It is a read-only agent (`Explore`, or any tool-restricted type), never a foreman worker. It gets the PR, the row's `acceptance`, the diff at that SHA, and the Critical / Important / Minor labels.
- It ends with one line that starts the last message: `VERDICT PASS <sha>` or `VERDICT FAIL <sha>`, then its findings. PASS means no Critical or Important finding.
- Serial mode dispatches it at step 11 on the final head SHA. Parallel mode's W4.2b reviewer carries the same tag and ends the same way, so its existing SHA-bound verdict is now recorded.

## The writer

In `subagent_stop` (the existing `SubagentStop` handler), before the worker report check:

1. Read the agent's first user message from `agent_transcript_path` (`transcript_head`). Match `^foreman-verifier: (\d+) ([0-9a-f]{7,40})$` on its first line. No match: not a verifier, carry on to the existing code.
2. Refuse when the same prompt is a `foreman-worker:` tag (a worker can't vouch for itself). The two tag grammars don't overlap, so this is a guard, not a branch.
3. Take `last_assistant_message`. Its first line must match `^VERDICT (PASS|FAIL) ([0-9a-f]{7,40})\b` and the SHA must equal, or be a prefix-compatible form of, the tag's SHA.
4. Resolve the tag's SHA with `git rev-parse --verify <sha>^{commit}` in the hook's cwd repo. It must give 40 characters.
5. Write the receipt. Only while a run is live (`h.active(repo, 'verdict_receipt')`), so a stray session can't plant receipts.
6. If the message has no valid verdict line, block the stop once (`stop_hook_active` guards the loop), telling the verifier the exact first line it owes. That is the same shape as `report_check`.

The main session can't write a receipt directly: the file is written by the hook process from a subagent's transcript, and no foreman CLI mints one.

## The gate

A new piece, `verdict_receipt`, switchable in `hooks_disabled`, separate from `merge_discipline` so each can be turned off alone. It runs after `merge_discipline` (so its missing-`--match-head-commit` text comes first).

It applies to `gh pr merge` only: `gt merge` and merging `gh api` calls are already denied during a run. It applies when the live run marker's `args` contain the word `loop` or `--auto-merge` (the marker stores the `/foreman` arguments). Parallel `--auto-merge` is covered by the same test. Manual runs, including `parallel N` without `--auto-merge`, are not gated: the human approves each PR at its head SHA.

For each such merge:

1. The PR number is the first all-digit positional of `gh pr merge`, else `number` from `pr_view`. With neither, deny: foreman can't tell which PR the verdict is for.
2. The SHA is the `--match-head-commit` value. It must be 7 to 40 hex characters.
3. Allow when a receipt `<pr>-<full sha>` exists whose full SHA starts with the given one and whose verdict is `PASS`. Otherwise deny:
   - no receipt: "no independent verdict for PR 5 at abc1234: dispatch a verifier whose prompt starts `foreman-verifier: 5 abc1234` and merge after it returns `VERDICT PASS`";
   - a FAIL receipt: "the verifier failed PR 5 at abc1234: fix its findings (step 8), then verify the new head";
   - the head moved: the old SHA's receipt doesn't match, so this is the "no receipt" case for the new SHA.

## Failure modes

- **Verifier crashes, times out or never answers:** no receipt, so the merge stays closed. SKILL.md step 11 stops the loop and notifies, as it does for any unmet auto-merge condition.
- **FAIL:** a step-8 / W4.4 finding. Two FAILs on one PR stop the loop.
- **A commit after the receipt:** a new SHA, so verify again.
- **Docs-only follow-ups:** each new head needs its own verdict. A carry-forward for exempt paths is not built; it is the first thing to add if that costs too much.
- **Hook errors:** fail open, like every other piece. Two cases fail closed because they are the point: a merge whose PR number can't be read, and a merge whose SHA can't be read.
- **Hooks disabled or no run marker:** nothing gates, as today.
- **Session moved to background, forked or resumed:** the run keeps its marker through an alias, and `marker()` follows it, so `args` survive.

## Tests

`tests/test_hooks.py`: the gate denies a serial auto-merge with no receipt, with a FAIL receipt, and at a different SHA than the receipt; allows with a PASS receipt at the SHA (full or 7-character prefix); does not apply in a manual run or a `parallel` run without `--auto-merge`; reads the PR number from `pr_view` when the command has none and denies when it has neither; is switched off by `hooks_disabled: [verdict_receipt]`; leaves `gt merge` and `gh api` merges to their own denials. The writer writes a receipt only for a tagged verifier whose message starts with a matching verdict line and whose SHA resolves; refuses a mismatched SHA, an unresolvable SHA, a worker-tagged agent, and a stop with no verdict line (blocked once, then allowed); writes nothing outside a live run.

`tests/run.sh`: the SKILL.md steps and the README row name the tag, the verdict line and `hooks_disabled: [verdict_receipt]`.

Not offline, and it spends quota: `tests/e2e-hooks.sh` with a real verifier, run last and once. A real verifier is a Claude subagent, so muse can't stand in for it; muse stays a reviewer in the chain.

## Open questions

None. The one judgment call, that a carry-forward for exempt paths is left out, is recorded above.
