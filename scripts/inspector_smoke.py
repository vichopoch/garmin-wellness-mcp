#!/usr/bin/env python3
"""Exercise the real MCP Inspector against a loopback-only synthetic server.

Run: uv run python scripts/inspector_smoke.py
Requires Node >=22.19 and npx. No Garmin/Auth0 credentials are read or sent.
The ephemeral signed JWT is passed in a mode-0600 temporary configuration file,
not CLI arguments. The Inspector version is pinned to the version tested here.
"""
from pathlib import Path
import json
import os
import logging
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.runtime_harness import running_runtime

INSPECTOR_VERSION = "2.9.0"


def main():
    logging.getLogger("mcp").setLevel(logging.WARNING)
    with running_runtime() as runtime, tempfile.TemporaryDirectory(prefix="garmin-inspector-") as tmp:
        config = Path(tmp) / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"wellness": {
            "type": "http", "url": runtime.url, "headers": {"Authorization": "Bearer " + runtime.token}
        }}}))
        config.chmod(0o600)
        env = {key: value for key, value in os.environ.items() if not key.startswith("MCP_")}
        env.update(MCP_STORAGE_DIR=tmp, MCP_INSPECTOR_SECRET_STORE="memory", MCP_INSPECTOR_PERSIST_TOKENS="none",
                   MCP_CLIENT_CONFIG_PATH=str(Path(tmp) / "client.json"))
        base = ["npx", "--yes", f"@modelcontextprotocol/inspector@{INSPECTOR_VERSION}", "--cli",
                "--config", str(config), "--server", "wellness", "--format", "json"]
        commands = [
            ("initialize", ["--method", "initialize"]),
            ("tools/list", ["--method", "tools/list", "--strict"]),
            ("get_profile", ["--method", "tools/call", "--tool-name", "get_profile"]),
            ("get_wellness_today", ["--method", "tools/call", "--tool-name", "get_wellness_today"]),
            ("get_sleep", ["--method", "tools/call", "--tool-name", "get_sleep", "--tool-arg", "date=2026-09-30"]),
            ("get_hrv", ["--method", "tools/call", "--tool-name", "get_hrv", "--tool-arg", "date=2026-09-30"]),
        ]
        results = {}
        for name, args in commands:
            output = subprocess.run(base + args, env=env, capture_output=True, text=True, timeout=90)
            if output.returncode:
                # Headers are never printed, including by exception formatting.
                print(json.dumps({"inspector": INSPECTOR_VERSION, "check": name,
                                  "status": "FAIL", "exit_code": output.returncode}))
                return 1
            result = json.loads(output.stdout)["result"]
            if result.get("isError"):
                raise RuntimeError(f"Inspector tool error: {name}")
            results[name] = result
        assert results["initialize"]["serverInfo"]["name"] == "Garmin Wellness"
        assert len(results["tools/list"]["tools"]) == 22
        assert results["get_profile"]["structuredContent"]["id"] == "garmin-profile-integration-fixture"
        assert results["get_sleep"]["structuredContent"]["sleep_score"] == 85
        assert results["get_hrv"]["structuredContent"]["nightly_avg"] == 55
        assert results["get_wellness_today"]["structuredContent"]["health"]["steps"] == 4321
        print(json.dumps({"inspector": INSPECTOR_VERSION, "transport": "streamable-http", "status": "PASS",
                          "checks": list(results), "tools": 22, "data_source": "synthetic Garmin",
                          "authorization": "ephemeral RS256 JWT through production verifier"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
