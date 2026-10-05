# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems.

Use GitHub's private vulnerability reporting instead: open the Security tab of this repository and choose "Report a vulnerability". Include the affected version or commit, steps to reproduce, and the impact you expect.

You will get an answer within a week. Once a fix is ready we will publish an advisory and credit you, unless you prefer not to be named.

## Supported versions

Only the latest release gets fixes.

## Scope

In scope: anything in this repository that could grant premium access without paying, accept forged webhooks, leak secrets or tokens, or let one user affect another user's account.

Out of scope: problems that need a leaked server secret, and deployments that ignore the production checklist in [docs/security.md](docs/security.md), for example running with `DEBUG=true`.
