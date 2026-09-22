# GitLab mirrors

[Run or inspect the mirror workflow](https://github.com/openresearchtools/gitlab-mirror/actions/workflows/mirror.yml) · [Latest sync status](https://github.com/openresearchtools/gitlab-mirror/blob/sync-status/status.json)

One GitHub-hosted workflow keeps 27 approved GitLab repositories synchronized with their GitHub sources. It runs daily at **03:17 UTC** and can be started at any time from **Actions → Sync GitLab mirrors → Run workflow → main**.

From a computer with GitHub CLI signed in as the owner:

```sh
gh workflow run mirror.yml --repo openresearchtools/gitlab-mirror --ref main
```

The computer only sends the trigger. All transfers run on GitHub's standard Ubuntu runner; no GitLab CI runner, GitLab card verification, local scheduled process, Git LFS, paid runner, or paid mirroring feature is used. The repository is public so standard hosted execution is free. No caches or uploaded artifacts are used.

## Mirroring behavior

- Every branch and tag, including annotated tags, is copied with its original object ID and reachable history. Commits, authors, timestamps, file contents, and GitHub workflow files are unchanged.
- Added branches/tags, forced updates, and deleted branches/tags propagate to GitLab. Default-branch changes are applied before deleting an old default branch.
- A lightweight comparison of references happens first. Unchanged repositories are not downloaded or pushed. Changed repositories are fetched on a temporary cloud runner, and Git uploads objects absent from the mirror.
- At most four reference checks and two transfers run concurrently. Transient Git/API failures have bounded retries. Overlapping workflow runs are serialized.
- The GitLab descriptions identify the source as a **Read-only mirror**, preserving the original description after the prefix. GitLab CI, shared runners, and LFS remain disabled on the mirrors.
- Empty repositories stay empty. Missing, renamed, newly private, or unexpectedly modified project identities cause a visible error; the job does not delete entire projects or overwrite unrelated repositories.
- Issues, pull requests, release attachments, package registries, Actions results, and LFS payloads are not plain Git history and are not mirrored.

## Scope

The exact source repository IDs and GitLab project IDs are in [mirrors.json](mirrors.json). `bashkitten-rust` is included. These remain excluded:

- `apt`
- `WildBuzzard`
- `bashkitten`
- `wildbuzzard-android`
- `termux-suite`

The automation repositories `gitlab-mirror` and `github-mirror-sync` are excluded to avoid mirroring their own controller code.

For security, newly created repositories are **not automatically enrolled**. To add one, create its plain GitLab mirror with CI and LFS disabled, add its immutable source/target IDs to `mirrors.json`, and replace the scoped GitLab token with one that includes that project. A repository name reused by a different owner/project cannot silently replace an enrolled source.

## Credential and workflow protections

- `MIRROR_GITLAB_TOKEN` is an encrypted GitHub **environment secret**, in `gitlab-mirrors`. The environment permits the `main` branch only.
- The GitLab token is restricted to the **27 selected mirror projects**. It has repository read/push and project metadata permissions, plus read-only inspection of branch protections. It has no user-wide scope, no project creation/deletion permission, and no access to other GitLab projects or groups.
- The only triggers are `schedule` and `workflow_dispatch`. There are no pull-request, issue-comment, `pull_request_target`, external dispatch, or workflow-run triggers. The workflow verifies the original GitHub repository ID, owner ID, `main` branch, and owner actor before running.
- Forks do not inherit this secret. Opening a pull request cannot run this privileged workflow. Only the owner is accepted for manual runs and reruns.
- `main` is protected against deletion/force-push, and proposed changes require the owner through CODEOWNERS. The account owner remains the trusted administrator and can maintain the repository.
- The checkout action is pinned to an immutable commit, checks out the triggering trusted commit, and does not persist credentials. The GitLab secret is injected only into the sync step, after the tests.
- The sync job's `GITHUB_TOKEN` has read-only contents access. It is not your personal GitHub CLI token and cannot push to your source repositories.
- Source repositories are never checked out and their code, hooks, workflows, submodules, or LFS filters are never executed. Git commands use argument arrays rather than a shell.
- Git and API redirects are refused. The credential helper releases the GitLab secret only for the exact `https://gitlab.com` host. Credentials never appear in remote URLs or repository configuration.
- A separate, fresh runner records non-secret status on `sync-status`. It receives only a repository-scoped GitHub token; it never receives the GitLab environment or secret. Regular status commits also prevent the public workflow becoming inactive after 60 days without repository activity.

Anyone given trusted write/admin access to automation code can potentially change a workflow to misuse its credentials. Do not grant that access to untrusted people or merge unreviewed workflow changes. These controls prevent ordinary public readers, forks, and pull-request authors from accessing the token; they do not replace account security.

The token expires on **2027-09-21**. Before then, replace the environment secret with an equivalent token restricted to the approved projects, run a successful manual sync, and revoke the old token. The retired GitLab CI token and schedule have been removed/disabled.

## Tests and status

```sh
python3 -m unittest -v test_mirror.py test_security.py
```

Tests exercise real Git branches, annotated tags, deletion, force-push, default-branch switching, and unchanged-run transfer skipping. Security tests cover rejected credential destinations, refused API redirects, repository identity mismatch, and immutable exclusions.

The Actions run log and job summary show each repository result. `sync-status/status.json` records the last run's result and links to its log. The workflow has a 15-minute transfer-job timeout and a 2-minute status-job timeout. A failed run remains visible and the next daily/manual run retries the synchronization.
