import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import shellscan  # noqa: E402

PR_FORM = '''gh pr create --title t --body "$(cat <<'EOF'
## Summary
- fixed "two words" here

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"'''
COMMIT_FORM = '''git commit -m "$(cat <<'EOF'
fix: thing

- "two words"
Co-Authored-By: a <a@b>
EOF
)"'''


def cmds(text, cwd='/r'):
    return shellscan.scan(text, cwd).cmds


def pairs(text, cwd='/r'):
    return [(c.prog, c.sub) for c in cmds(text, cwd)]


class DetectTest(unittest.TestCase):
    def test_wrappers_do_not_hide_the_program(self):
        for t in ('nohup git push', 'timeout 60 git push', 'sudo git push', 'xargs -n1 git push',
                  'nice -n 5 git push', 'env -i git push', 'command -p git push', 'stdbuf -oL git push',
                  'caffeinate git push', '/usr/bin/git push', "'git' push", '\\git push'):
            self.assertEqual(pairs(t), [('git', 'push')], t)
        self.assertEqual(pairs(r'find . -exec git add {} \;'), [('git', 'add')])

    def test_substitutions_shells_eval_and_shell_heredocs(self):
        for t in ('x="$(git push)"', 'x=`git push`', "bash -lc 'git push'", "bash -c 'git push'", 'eval "git push"',
                  "bash <<'EOF'\ngit push\nEOF", "sh -s <<EOF\ngit push\nEOF"):
            self.assertEqual(pairs(t), [('git', 'push')], t)

    def test_unquoted_substitution_is_one_word_and_scanned_once(self):
        self.assertEqual(pairs('x=$(cd sub && git push) ; git status'), [('git', 'push'), ('git', 'status')])
        self.assertEqual(cmds('x=$(cd sub && git push)')[0].cwd, '/r/sub')

    def test_gh_repo_flags_and_api(self):
        c = cmds('gh pr -R o/r merge 1')[0]
        self.assertEqual((c.sub, c.args, c.ghrepo), ('pr merge', ['1'], 'o/r'))
        c = cmds('gh api -X PUT repos/o/r/pulls/1/merge')[0]
        self.assertEqual(c.sub, 'api')
        self.assertTrue(shellscan.gh_api_merge(c.args, False))

    def test_git_dash_C_with_a_quoted_dir(self):
        c = cmds('git -C "my dir" push')[0]
        self.assertEqual((c.sub, c.cwd), ('push', '/r/my dir'))

    def test_cd_into_a_dir_with_spaces(self):
        self.assertEqual(cmds('cd "my wt" && git push')[0].cwd, '/r/my wt')

    def test_pr_form_gives_known_args_and_its_heredoc(self):
        c = cmds(PR_FORM)[0]
        self.assertEqual(c.sub, 'pr create')
        self.assertIsNotNone(c.args)
        self.assertIn('--body', c.args)
        self.assertTrue(any('🤖 Generated with' in h for h in c.heredocs))
        self.assertTrue(any('"two words"' in h for h in c.heredocs))

    def test_commit_form_heredoc_belongs_to_the_commit(self):
        c = cmds(COMMIT_FORM)[0]
        self.assertEqual(c.sub, 'commit')
        self.assertIsNotNone(c.args)
        self.assertTrue(any('Co-Authored-By' in h for h in c.heredocs))

    def test_heredoc_body_with_stray_quotes_keeps_args_known(self):
        t = PR_FORM.replace('- fixed', "- don't keep a stray \" quote\n- fixed")
        sc = shellscan.scan(t, '/r')
        self.assertTrue(sc.ok)
        self.assertIsNotNone(sc.cmds[0].args)
        self.assertTrue(any('stray' in h for h in sc.cmds[0].heredocs))

    def test_quoted_git_push_is_not_a_push(self):
        self.assertEqual(pairs('git commit -m "fix: git push retry"'), [('git', 'commit')])
        self.assertEqual(pairs("echo 'git push'"), [])

    def test_unquoted_echo_git_push_is_an_accepted_false_deny(self):
        self.assertEqual(pairs('echo git push'), [('git', 'push')])

    def test_cd_in_a_group_is_scoped(self):
        a, b = cmds('(cd wt && git push); git push')
        self.assertEqual((a.cwd, b.cwd), ('/r/wt', '/r'))
        self.assertEqual(cmds('(cd wt && git push)')[0].cwd, '/r/wt')
        self.assertEqual(cmds('cd wt && git push')[0].cwd, '/r/wt')
        self.assertIsNone(cmds('cd $X && git push')[0].cwd)
        self.assertIsNone(cmds('cd - && git push')[0].cwd)

    def test_revision_3_forms(self):
        self.assertEqual(pairs('# note\ngit push'), [('git', 'push')])
        self.assertEqual(pairs('true;(git push)'), [('git', 'push')])
        self.assertEqual(pairs('{ git push; }'), [('git', 'push')])
        self.assertEqual(pairs('if true; then git push; fi'), [('git', 'push')])
        self.assertEqual(pairs('git push |& tee log'), [('git', 'push')])
        self.assertTrue(cmds('env -i git push')[0].env)
        self.assertIsNone(cmds('GIT_DIR=x git push')[0].cwd)
        self.assertEqual(cmds('GIT_DIR=x git push')[0].assigns, ['GIT_DIR'])
        self.assertIsNone(cmds('git --git-dir=x push')[0].cwd)
        self.assertEqual(cmds("git commit -m 'a;b'")[0].args, ['-m', 'a;b'])

    def test_heredoc_body_is_blanked(self):
        sc = shellscan.scan("cat > /tmp/m <<'EOF'\ndon't git push +main :old\nEOF\ngit commit -F /tmp/m", '/r')
        self.assertTrue(sc.ok)
        self.assertEqual([(c.prog, c.sub) for c in sc.cmds], [('git', 'commit')])
        self.assertEqual(sc.docs[0].target, '/tmp/m')
        self.assertIn('+main', sc.docs[0].body)

    def test_stdin_heredoc_feeds_the_commit(self):
        c = cmds('git commit -F - <<EOF\nmsg\nCo-Authored-By: x\nEOF')[0]
        self.assertEqual(c.heredocs, ['msg\nCo-Authored-By: x\n'])

    def test_comment_is_blanked_from_the_words(self):
        self.assertEqual(cmds('git push -u origin HEAD  # note')[0].args, ['-u', 'origin', 'HEAD'])

    def test_braces_inside_a_word_do_not_split(self):
        c = cmds('gh api repos/{owner}/{repo}/pulls/1/merge -X PUT')[0]
        self.assertTrue(shellscan.gh_api_merge(c.args, False))

    def test_unbalanced_quote_falls_back_to_unknown_args(self):
        sc = shellscan.scan('git push "oops', '/r')
        self.assertFalse(sc.ok)
        self.assertEqual([(c.prog, c.sub, c.args) for c in sc.cmds], [('git', 'push', None)])
        sc = shellscan.scan('cat <<EOF\nno end\ngit push', '/r')
        self.assertFalse(sc.ok)
        self.assertEqual([c.sub for c in sc.cmds], ['push'])

    def test_redirect_ampersand_does_not_split(self):
        self.assertEqual(pairs('git push 2>&1 | tail -1'), [('git', 'push')])


class ClassifyTest(unittest.TestCase):
    def test_gt_aliases(self):
        self.assertEqual(pairs('gt ss'), [('gt', 'submit')])
        self.assertEqual(pairs('gt s'), [('gt', 'submit')])
        self.assertEqual(pairs('gt m -a'), [('gt', 'modify')])
        self.assertEqual(pairs('gt c -m x'), [('gt', 'create')])

    def test_pull_rebase_is_a_rebase(self):
        self.assertTrue(shellscan.is_rebase(cmds('git pull --rebase')[0]))
        self.assertTrue(shellscan.is_rebase(cmds('git pull -r origin main')[0]))
        self.assertTrue(shellscan.is_rebase(cmds('git rebase main')[0]))
        self.assertFalse(shellscan.is_rebase(cmds('git pull')[0]))
        self.assertFalse(shellscan.is_rebase(cmds('git pull --rebase=false')[0]))

    def test_push_info(self):
        def info(t):
            return shellscan.push_info(cmds(t)[0].args, 'main')
        self.assertEqual(info('git push -u origin HEAD'), (False, False, False))
        self.assertEqual(info('git push origin :old'), (True, False, False))
        self.assertEqual(info('git push --delete origin b'), (True, False, False))
        self.assertEqual(info('git push -d origin b'), (True, False, False))
        self.assertEqual(info('git push --force'), (False, True, False))
        self.assertEqual(info('git push -f'), (False, True, False))
        self.assertEqual(info('git push -uf origin HEAD'), (False, True, False))
        self.assertEqual(info('git push --force-with-lease'), (False, True, False))
        self.assertEqual(info('git push --mirror'), (False, True, False))
        self.assertEqual(info('git push origin +feat'), (False, True, False))
        self.assertEqual(info('git push origin HEAD:main'), (False, False, True))
        self.assertEqual(info('git push origin main'), (False, False, True))
        self.assertEqual(info('git push origin HEAD:refs/heads/main'), (False, False, True))
        self.assertEqual(info('git push origin feat 2>&1'), (False, False, False))

    def test_gh_api_merge_by_method_and_path(self):
        def merge(t, subagent=False):
            return shellscan.gh_api_merge(cmds(t)[0].args, subagent)
        self.assertFalse(merge('gh api repos/o/r/pulls/1/merge'))
        self.assertFalse(merge('gh api -X DELETE repos/o/r/git/refs/heads/x'))
        self.assertTrue(merge('gh api --method PUT repos/o/r/pulls/$N/merge'))
        self.assertTrue(merge('gh api repos/o/r/merges -f base=main -f head=x'))
        self.assertTrue(merge('gh api -X PATCH repos/o/r/git/refs/heads/main -f sha=abc'))
        self.assertTrue(merge("gh api graphql -f query='mutation { mergePullRequest(input:{}) { clientMutationId } }'"))
        self.assertTrue(merge("gh api graphql -f query='mutation { enablePullRequestAutoMerge(input:{}) { clientMutationId } }'"))
        self.assertFalse(merge("gh api graphql -f query='query { viewer { login } }'"))
        self.assertFalse(merge('gh api graphql -F query=@q.graphql'))
        self.assertTrue(merge('gh api graphql -F query=@q.graphql', subagent=True))
        self.assertTrue(merge('gh api graphql --input q.json', subagent=True))
        self.assertTrue(shellscan.gh_api_merge(None, False))


class SegmentTest(unittest.TestCase):
    def test_one_paren_wrap_with_cd(self):
        sc = shellscan.scan('(cd /x && /p/review.sh a b)', '/r')
        words = [(s.words, s.sep, s.cwd) for s in sc.segs if s.words]
        self.assertEqual(words, [(['cd', '/x'], '&&', '/r'), (['/p/review.sh', 'a', 'b'], ')', '/x')])

    def test_quoted_operators_stay_in_one_segment(self):
        sc = shellscan.scan("/p/gate-lock.sh w1 -- 'npm run lint && npm test'", '/r')
        self.assertEqual([s.words for s in sc.segs if s.words], [['/p/gate-lock.sh', 'w1', '--', 'npm run lint && npm test']])


if __name__ == '__main__':
    unittest.main()
