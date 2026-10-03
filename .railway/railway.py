from railway_sdk import define_railway, postgres, preserve, project, service, volume


@define_railway
def main(ctx=None):
    Postgres = postgres("Postgres", region="us-west2")
    # networking overrides: { privateNetworkEndpoint: "postgres" }
    postgresVolume = volume("postgres-volume", { "alerts": { "usage": { "100": {}, "80": {}, "95": {} } }, "allowOnlineResize": True, "region": "us-west2", "sizeMB": 50000 })
    garminMcpVolume = volume("garmin-mcp-volume", { "alerts": { "usage": { "100": {}, "80": {}, "95": {} } }, "allowOnlineResize": True, "region": "us-west2", "sizeMB": 50000 })
    garminMcp = service(
        "garmin-mcp",
        healthcheck="/healthz",
        healthcheckTimeout=120,
        replicas={ "us-west2": 1 },
        volumeMounts={ "/data/garmin": garminMcpVolume },
        env={ "AUTH0_ALLOWED_SUBJECT": preserve(), "AUTH0_AUDIENCE": preserve(), "AUTH0_DOMAIN": preserve(), "AUTH0_ISSUER": preserve(), "AUTH0_JWKS_URL": preserve(), "AUTH_MODE": preserve(), "CHATGPT_TOOLSET": preserve(), "DATABASE_URL": preserve(), "GARMINTOKENS": preserve(), "GARMIN_INITIAL_BACKFILL_DAYS": preserve(), "GARMIN_MCP_HOST": preserve(), "GARMIN_MCP_TRANSPORT": preserve(), "GARMIN_READ_ONLY": preserve(), "GARMIN_SYNC_ENABLED": preserve(), "GARMIN_SYNC_LOOKBACK_DAYS": preserve(), "GARMIN_TIMEZONE": preserve(), "LOG_LEVEL": preserve(), "LOG_SENSITIVE_DATA": preserve(), "MCP_RESOURCE_URL": preserve() },
    )
    return project("garmin-wellness-mcp", resources=[Postgres, garminMcp, postgresVolume, garminMcpVolume])
