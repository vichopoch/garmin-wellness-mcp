# Railway infrastructure snapshot

`railway.py` describes the deployed project using the official Python SDK. Existing
variable values use `preserve()` so credentials are never copied into Git.

Preview without changing the deployment:

```sh
uv tool run --from railway-sdk railway config plan
```

Apply only a reviewed plan:

```sh
uv tool run --from railway-sdk railway config apply
```

The initial verified plan contains no changes. Do not use `--include-variables`
when importing: it may put secrets in the generated file. The CLI's Python import
had unquoted mapping keys; those were corrected and the plan verified.

Deploy application code independently with `railway up --service garmin-mcp`.
