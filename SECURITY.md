# Security policy

HomeCompute contains privileged setup scripts and security-sensitive
infrastructure design. Please report vulnerabilities privately so maintainers
have an opportunity to assess them before public disclosure.

## Supported versions

HomeCompute has not reached a stable release. Security fixes currently target
the latest revision of the default branch only.

## Reporting a vulnerability

Use GitHub's **Security → Report a vulnerability** flow after the repository is
published and private vulnerability reporting is enabled. Include:

- the affected file, command, or trust boundary;
- prerequisites and a minimal reproduction;
- potential impact;
- whether secrets or personal data may have been exposed;
- a suggested mitigation, if known.

Do not open a public issue containing exploit details, credentials, real
infrastructure values, personal data, or sensitive logs. If private
vulnerability reporting is unavailable, contact a maintainer through their
GitHub profile without including sensitive details and request a private
reporting channel.

No response-time promise is made while the project is pre-release, but reports
will be assessed according to impact and exploitability. Coordinate public
disclosure with the maintainers.

## Security boundaries

The following are security requirements, not optional deployment advice:

- production secrets and environment files stay outside the repository;
- non-loopback inference binds require a verified firewall or VPN allow-list;
- clients receive distinct, revocable credentials;
- logs exclude prompts, responses, tool data, audio, and authorization values;
- Home Assistant remains authoritative for validating and executing actions;
- private workloads do not receive implicit cloud fallback.

Operational incidents in a private deployment should first be contained by its
operator: revoke affected credentials, isolate exposed services, preserve
sanitized evidence, and return to the last qualified release tuple.
