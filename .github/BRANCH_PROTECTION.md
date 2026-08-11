# Required GitHub repository controls

Configure a branch ruleset for `master` in GitHub. Repository files cannot
enforce these server-side settings by themselves.

- Require pull requests; disallow direct pushes and force pushes.
- Require at least one approval and approval from Code Owners.
- Dismiss stale approvals when the diff changes.
- Require conversation resolution before merge.
- Require the branch to be up to date before merge.
- Require the `Lint & Test` and `Build, Test & Security Scan` status checks.
- Block bypass for administrators and repository roles where the plan permits.
- Restrict deletion of `master` and require signed commits if operationally
  available.

Configure the `production` environment separately:

- At least one required reviewer who is not the deployment initiator.
- Prevent self-review and restrict deployment to `master`.
- Keep production secrets only in the environment, not repository secrets.
- Do not grant environment bypass to automation accounts.

After configuration, capture the ruleset and environment settings in the
release evidence. Do not mark P0.6 complete based only on this document.
