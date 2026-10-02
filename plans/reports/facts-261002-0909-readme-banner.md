# Facts for the README banner

Every on-screen label in `assets/videos/readme-banner/` traces to one of these
lines in the repository.

| On-screen label | Source |
|---|---|
| Git to SVN, one functional group at a time | `skills/git-to-svn-transfer/SKILL.md`: "commit it to SVN by hand, one functional group at a time" |
| Split a Git range into functional groups | `README.md`: "Splits a Git range, or a first import, into functional groups from a plan." |
| Branch `svn-transfer/<run-id>`, one worktree per group | `SKILL.md`, Boundaries: "one branch `svn-transfer/<run-id>` and one worktree registration per group" |
| Every cumulative state is built and tested; build, test, smoke | `README.md`: "Runs the project's build, test and smoke checks on every cumulative worktree" |
| `verify --run-checks` | `SKILL.md`, Mode: verify |
| ready, failed, blocked, unverified | `SKILL.md`, readiness table |
| Delta package, `message.txt`, `SVN_MANUAL_TRANSFER.md` | `SKILL.md`, Mode: prepare |
| apply, precommit, record | `SKILL.md`, "The SVN side" |
| You commit to SVN; it never runs `svn commit` or `git push` | `README.md`: "It never runs `svn commit` or `git push`. You commit to SVN yourself." |
| Nothing missing. Nothing repeated. | `README.md`: "Verifies that no change is missing or repeated" |
| `/plugin install git-to-svn-transfer@git-to-svn-transfer` | `README.md`, Install |

Revision numbers shown in the hand-off scene (r101 to r103) are illustrative.
