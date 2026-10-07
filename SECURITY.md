# Security Policy

OpenCS 0.1.0a0 is an offline development preview. No version is supported for
production use, and no security response SLA is promised.

Do not post secrets, customer data or exploit details in public issues. Report
vulnerabilities privately through the repository's Security tab, using
[Report a vulnerability](https://github.com/Sealdot/opencs/security/advisories/new).
If that route is unavailable, email [sealdot404@gmail.com](mailto:sealdot404@gmail.com).
The maintainer has authorized these routes; private vulnerability reporting must
be enabled and checked as part of repository publication.

Use only synthetic fixtures and ignored local credentials. The in-memory service,
loopback listener, fake providers and contract tests do not establish production
security, durable isolation or deployment readiness.
