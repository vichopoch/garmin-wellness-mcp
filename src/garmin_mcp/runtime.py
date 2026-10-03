"""Production composition retaining upstream FastMCP, SDK and reviewed registry."""
from contextlib import asynccontextmanager
import asyncio
import os
from pathlib import Path
import uuid
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from garmin_mcp.garmin_client import TokenClient
from garmin_mcp.security import OAuthSettings, OAuthMiddleware
from garmin_mcp.observability import configure_private_logging, log_event
from garmin_mcp.storage import Store, migrate


def config():
    from garmin_mcp.read_only import read_only_enabled
    if not read_only_enabled():
        raise ValueError('Production runtime requires GARMIN_READ_ONLY=true')
    if os.getenv('AUTH_MODE','oauth') != 'oauth':
        raise ValueError('Production runtime requires AUTH_MODE=oauth')
    if os.getenv('CHATGPT_TOOLSET','wellness') != 'wellness':
        raise ValueError('Production runtime only exposes the reviewed wellness toolset')
    port = int(os.getenv('PORT') or os.getenv('GARMIN_MCP_PORT') or '8000')
    if not 1 <= port <= 65535:
        raise ValueError('Invalid server port')
    return ('0.0.0.0' if os.getenv('PORT') or os.getenv('RAILWAY_ENVIRONMENT_ID') else os.getenv('GARMIN_MCP_HOST','127.0.0.1'),port)


def profile_id(token_path):
    """Random local profile identifier persists with tokens and reveals no email."""
    path = Path(token_path)
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    target = path / 'profile-id'
    if not target.exists():
        try:
            fd = os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd,'w') as f:
                f.write('garmin-profile-' + str(uuid.uuid4()))
    return target.read_text().strip()


def build_app(client=None, store=None, settings=None, identifier=None):
    from urllib.parse import urlsplit
    from garmin_mcp import _ToolFilter, _resolve_tool_filters
    from garmin_mcp.wellness import WellnessService, register_tools
    config()
    settings = settings or OAuthSettings.from_env()
    client = client or TokenClient()
    service = WellnessService(client,store=store,timezone=os.getenv('GARMIN_TIMEZONE','America/Santiago'),profile_id=identifier or profile_id(os.getenv('GARMINTOKENS','/data/garmin')),call_timeout=float(os.getenv('GARMIN_MCP_CALL_TIMEOUT','90')))

    @asynccontextmanager
    async def lifespan(app):
        task = None
        if store and os.getenv('GARMIN_SYNC_ENABLED','false').lower() == 'true':
            from garmin_mcp.sync import background_sync
            task = asyncio.create_task(background_sync(service,store,client))
        try:
            yield {}
        finally:
            if task:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass

    public_host = urlsplit(settings.resource_url).netloc
    app = FastMCP('Garmin Wellness',stateless_http=True,json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=['127.0.0.1:*','localhost:*','testserver'] + ([public_host] if public_host else []),
            allowed_origins=['http://127.0.0.1:*','http://localhost:*'] + (['https://'+public_host] if public_host else [])))
    register_tools(_ToolFilter(app,*_resolve_tool_filters()),service)
    if not app._tool_manager.list_tools():
        raise RuntimeError("No reviewed Wellness tools registered; refresh implementation audit before deployment")

    @app.custom_route('/healthz',methods=['GET','HEAD'])
    async def healthz(request):
        return JSONResponse({'status':'ok'})
    http_app = app.streamable_http_app()
    transport_lifespan = http_app.router.lifespan_context

    @asynccontextmanager
    async def http_lifespan(starlette_app):
        # FastMCP's lifespan runs per stateless MCP request. The importer must
        # instead live for the ASGI process, including when no client connects.
        async with transport_lifespan(starlette_app):
            async with lifespan(app):
                yield

    http_app.router.lifespan_context = http_lifespan
    return OAuthMiddleware(http_app,settings), app, service


def main():
    import uvicorn
    configure_private_logging()
    os.umask(0o077)
    host,port = config()
    url = os.getenv('DATABASE_URL')
    store = None
    if url:
        migrate(url)
        store = Store(url)
    app,_,_ = build_app(store=store)
    log_event('startup',status='started')
    uvicorn.run(app,host=host,port=port,access_log=False,log_config=None)

if __name__ == '__main__':
    main()
