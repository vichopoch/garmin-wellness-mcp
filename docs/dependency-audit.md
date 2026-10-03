# Dependency review

Checked 2026-10-02 (America/Santiago) against live PyPI JSON and official releases. Search-result snippets were stale; the package index is authoritative for published versions.

| Dependency | Upstream | Latest stable at review | Selected |
|---|---|---|---|
| Python | `>=3.10` metadata | Not an automatic major/minor upgrade target | `>=3.12`, required by Garmin 0.3.17 |
| garminconnect | `==0.3.2` | `0.3.17`, published 2026-09-29 | `==0.3.17` |
| mcp | `>=1.28.1,<2` | `2.3.0`, published 2026-10-02 22:06 UTC | `==1.28.1` |

The Garmin upgrade is deliberate: 0.3.10 and 0.3.11 address token-store permissions, symlinks, atomic writes and concurrent refresh; 0.3.17 also improves upgraded-token errors and introduces training-load reads. Its change from zero-based to one-based goals/challenge pagination needs attention. The upstream goals adapter and all four affected challenge tools already default to `start=1`; ad-hoc challenges correctly remain zero-based. A regression test now exercises these tool defaults through the real 0.3.17 SDK with only its HTTP boundary mocked, rather than mocking away parameter validation. New read calls include `get_training_four_week_load_balance`, `get_daily_training_status` and `get_training_load_activities`. Availability still depends on Garmin account/device data.

The selected MCP SDK preserves upstream `mcp.server.fastmcp.FastMCP`, Streamable HTTP, OAuth resource-server integration, `ToolAnnotations`, and explicit structured output. SDK 2.x is a separate API migration; its `MCPServer` API and new protocol behavior are not assumed compatible. Exact 1.28.1 pin replaces the floating range. Upstream string-returning handlers keep `structured_output=False`; wellness dict-returning handlers explicitly select structured output and read-only annotations. Lock regeneration and the final test results are recorded by the main implementation report, not inferred from release notes.

The installed Garmin API and native HTTP client source are fingerprinted in the read-only audit. Dependency updates must be reviewed together with that manifest; a version or source mismatch makes registration fail closed. `Client.connectapi()` and `Client.download()` terminate in GET; the generic POST GraphQL helper is never included in the production client's method allowlist.

Sources:

- [Garmin PyPI JSON](https://pypi.org/pypi/garminconnect/json)
- [Garmin 0.3.17 release](https://github.com/cyberjunky/python-garminconnect/releases/tag/0.3.17)
- [Garmin 0.3.10 security hardening](https://github.com/cyberjunky/python-garminconnect/releases/tag/0.3.10)
- [Garmin 0.3.11 token-store hardening](https://github.com/cyberjunky/python-garminconnect/releases/tag/0.3.11)
- [MCP PyPI JSON](https://pypi.org/pypi/mcp/json)
- [MCP 1.28.1 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.28.1)
- [MCP 2.3.0 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0)
- [MCP 1.28.1 source](https://github.com/modelcontextprotocol/python-sdk/tree/v1.28.1)

## Final security lock refresh (2026-10-03)

`pip-audit` found known advisories in inherited AnyIO4.8, Click8.1.8, h11 0.14,
PyJWT2.13 and urllib3 2.7. The lock was regenerated with security-fixed releases
while retaining Garmin0.3.17 and MCP1.28.1. PyJWT is explicitly pinned2.15.1;
AnyIO4.15.1, h11 0.16.0, urllib3 2.8.0 and all transitive versions are fixed in
uv.lock. Hatchling build backend is pinned1.32.4.

After this update: full suite849passed,20legacy live tests deselected.
`pip-audit` on the exported frozen production lock:42packages,0known advisories.
This is the result at audit time, not a guarantee against undiscovered issues.
