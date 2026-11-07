# Release Process

(Maintainers only.)

Releases are created from draft GitHub releases created by
[Release Drafter](https://github.com/release-drafter/release-drafter). When a release is published,
GitHub Actions will automatically:

1. Extract the version from the release tag (e.g. `v1.2.3`)
2. Run tests and checks
3. Build and publish the package to PyPI using `uv publish` and
    [Trusted Publisher](https://docs.pypi.org/trusted-publishers/) on PyPI
4. Build the docs from the release tag and publish them to [cgt-calc.uk](https://cgt-calc.uk/)

Publishing a pre-release does not change cgt-calc.uk. Each deployment to cgt-calc.uk goes through
the `docs` environment, which requires a reviewer, so the deploy job waits until one approves it in
the workflow run.

## Fix the released docs without a release

The docs on `main` are published to [next.cgt-calc.uk](https://next.cgt-calc.uk/). To correct the
docs of the latest release on cgt-calc.uk before the next release:

1. Merge the fix to `main` as usual.
2. Create a branch named `docs/<tag>` from the latest release tag, for example `docs/v4.0.0` from
    `v4.0.0`. The branch may change only files under `docs/`.
3. For each commit whose docs fix applies to that release, copy only its changes under `docs/`. Most
    commits also change code, tests or the spelling lists, so leave those out:

    ```shell
    git cherry-pick --no-commit <commit>
    git restore --source=HEAD --staged --worktree -- . ':!docs/'
    git commit -C <commit>
    ```

4. Push the branch to this repository.
5. Open **Actions → Release docs → Run workflow**, keep **Use workflow from** set to `main`, and
    enter `docs/v4.0.0` as the ref to publish.

The workflow refuses any other ref, including `main`. It also refuses a branch that does not contain
the release tag or that changes files outside `docs/`. A fix that also needs a change to
`zensical.toml`, such as a new page, a navigation change or a redirect, has to wait for the next
release. To publish a release’s docs again, for example after a failed run, enter its tag, such as
`v4.0.0`.
