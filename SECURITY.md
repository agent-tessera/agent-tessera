# Security policy

Agent Tessera is a security-sensitive project: it issues credentials and
enforces permissions for AI agents. We take reports seriously.

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Report a vulnerability" on the repository's **Security** tab).
Do not open public issues, discussions, or pull requests for security problems.

Include a description, affected versions, reproduction steps, and the impact
you expect. We aim to acknowledge reports within 3 business days and to agree
on a disclosure timeline with you.

## Supported versions

Until version 1.0, only the latest release receives security fixes.

## Scope

Agent Tessera enforces policy inside the agent's own process. An agent whose
code is already hostile can disable the SDK; that is a documented limit, not a
vulnerability (see [docs/threat-model.md](docs/threat-model.md)). In scope
are, for example, ways to get a decision the policy does not allow through
governed calls, forge or replay credentials or policy bundles, tamper with the
audit log without detection, or reach the server without authentication.
