# Garmin Wellness MCP

MCP personal de lectura para consultar sueño, recuperación, salud y entrenamiento
Garmin desde ChatGPT. Conserva FastMCP y los adaptadores de
[Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp), con OAuth obligatorio,
23 herramientas Wellness, almacenamiento normalizado y deployment en Railway.

```text
ChatGPT → OAuth Auth0 → HTTPS /mcp en Railway → Garmin Connect → reloj Garmin
                                  ↓
                         PostgreSQL normalizado
```

## Deployment y estado verificado

| Recurso | Valor |
| --- | --- |
| Repositorio | [vichopoch/garmin-wellness-mcp](https://github.com/vichopoch/garmin-wellness-mcp) |
| Rama | `garmin-wellness-railway` |
| Railway project | `garmin-wellness-mcp` · `37e7799e-4bdd-4b3d-a6dc-0a635c1eb261` |
| Servicio / entorno | `garmin-mcp` / `production` |
| MCP URL | **https://garmin-mcp-production-fe35.up.railway.app/mcp** |
| Health | [GET /healthz](https://garmin-mcp-production-fe35.up.railway.app/healthz) |
| OAuth metadata | [Protected resource](https://garmin-mcp-production-fe35.up.railway.app/.well-known/oauth-protected-resource) |
| Persistencia Garmin | Volume `garmin-mcp-volume`, montado en `/data/garmin` |
| Base de datos | Servicio Railway `Postgres` |

Verificación externa del 2026-10-03, zona America/Santiago: health **200**;
GET/POST `/mcp` sin credenciales **401**; metadata OAuth **200**. El handshake,
listado y llamadas MCP autenticadas pasan con un servidor local real y datos
Garmin simulados. MCP Inspector **2.9.0** también verifica los esquemas en modo
estricto. Estos tests no demuestran disponibilidad de métricas de una cuenta real.

Auth0 publica PKCE S256 y RFC 9207. La comprobación sin sesión (`prompt=none`)
devuelve `login_required` después de corregir la autorización de la aplicación;
el flujo interactivo real de ChatGPT quedó verificado el 2026-10-03: conexión
lista, peticiones autenticadas de `openai-mcp/1.0.0` con respuesta 200 y llamada
exitosa a `get_profile`. Fue necesario definir `garmin:read` en la API y concederlo
al cliente en User-Delegated Access, además de usar la identidad exacta autorizada.
La autenticación Garmin y llamadas reales también se verificaron desde Railway,
incluidos tokens persistentes tras redeploy; el informe privado de ejecución
documenta la cobertura del backfill. Un health 200 comprueba el proceso, no el
login Garmin ni la disponibilidad de todas las métricas en cada fecha.

## Seguridad y alcance

- Producción exige `GARMIN_READ_ONLY=true`, `CHATGPT_TOOLSET=wellness` y
  `AUTH_MODE=oauth`; nunca hay fallback HTTP anónimo.
- Cada petición a `/mcp`, incluida la barra final, pasa por firma RS256/JWKS,
  issuer, audience, expiración, nbf, scope `garmin:read` y el `sub` exacto del
  propietario configurado en `AUTH0_ALLOWED_SUBJECT`.
- Sólo `/healthz` y discovery son públicos. Configuración OAuth incompleta deja
  MCP cerrado; no se exponen herramientas de escritura.
- El [registro auditado](docs/tool-audit.md) clasifica implementación y llamadas
  SDK. Una herramienta desconocida, mutadora o cuyo código cambió no se registra,
  aunque esté en una allowlist. La frontera Garmin tiene otra allowlist de
  métodos de lectura. Actualizar SDK/código exige revisar sus fingerprints.
- Logs JSON contienen herramienta, duración, estado, request ID y tipo de error;
  omiten argumentos, resultados, email, contraseña, MFA, cookies y tokens.
- No se guarda contraseña Garmin. Tokens refrescados permanecen en el volume;
  directorio 0700, archivos 0600, proceso UID/GID 10001. No se guardan tracks GPS.
- Es un servicio de un propietario; no es una plataforma Garmin multiusuario.
  Las estadísticas son descriptivas y no sirven para diagnosticar enfermedades.

La configuración completa del proveedor está en [OAuth/Auth0](docs/auth0.md).
`AUTH0_AUDIENCE` y `MCP_RESOURCE_URL` deben ser exactamente la URL pública `/mcp`.

## Desarrollo local

Requiere Python 3.12 o 3.13 y [uv](https://docs.astral.sh/uv/). El lock fija todas
las dependencias; Garmin pasó de **0.3.2 a 0.3.17** y MCP permanece en **1.28.1**.
Motivos, compatibilidad y fuentes: [dependency audit](docs/dependency-audit.md).

```sh
git clone https://github.com/vichopoch/garmin-wellness-mcp.git
cd garmin-wellness-mcp
git switch garmin-wellness-railway
uv sync --frozen
cp .env.example .env
```

Edite `.env` con su configuración OAuth, sin contraseña Garmin ni client secret
Auth0. Para datos locales fuera del repositorio y SQLite de desarrollo:

```sh
export GARMINTOKENS="$HOME/.local/share/garmin-wellness"
export GARMIN_MCP_HOST=127.0.0.1
export GARMIN_SYNC_ENABLED=false
export DATABASE_URL="sqlite:///$PWD/wellness.db"
uv run --env-file .env garmin-wellness
```

Sin configurar Auth0, health sigue funcionando y MCP responde 401. Para comprobar
el protocolo sin cuenta Garmin/Auth0, use el harness de tests; sólo escucha en
loopback y valida un JWT efímero firmado, sin introducir bypass en producción:

```sh
uv run pytest
uv run python scripts/inspector_smoke.py
```

Inspector requiere Node ≥22.19 y usa `npx @modelcontextprotocol/inspector@2.9.0`.
Verifica initialize, tools/list, perfil, wellness, sueño y HRV. Los tests e2e
upstream con credenciales reales no se ejecutan por defecto. Baseline original:
[docs/baseline.txt](docs/baseline.txt).

## Login Garmin y persistencia

En una terminal privada local:

```sh
uv run garmin-mcp-auth --token-path "$HOME/.local/share/garmin-wellness"
uv run garmin-mcp-auth --token-path "$HOME/.local/share/garmin-wellness" --verify
```

Introduzca email, contraseña y MFA directamente en esa terminal. Contraseña y MFA
no se muestran. El comando escribe `garmin_tokens.json` y verifica acceso al perfil;
no publique el contenido ni lo pegue en ChatGPT.

Para autenticar directamente en el volume Railway:

```sh
railway ssh --service garmin-mcp --environment production -- /app/.venv/bin/python /app/container-entrypoint.py garmin-mcp-auth --token-path /data/garmin
railway ssh --service garmin-mcp --environment production -- /app/.venv/bin/python /app/container-entrypoint.py garmin-mcp-auth --token-path /data/garmin --verify
```

El entrypoint deja el directorio con propietario correcto y ejecuta el comando
como `garmin`. En el Mac de este despliegue está registrada la clave dedicada
`~/.ssh/id_ed25519_railway_garmin`; añada `-i ~/.ssh/id_ed25519_railway_garmin`
a `railway ssh` para seleccionarla. La clave privada permanece fuera del repositorio. Después de redeployar, repita `--verify`: debe funcionar sin contraseña.
No use `railway run` para este bootstrap remoto: ejecutaría el comando localmente.

Si SSH/MFA no funciona, autentique localmente y transfiera sólo el archivo de
tokens mediante un canal privado autorizado, al mismo volume y con propietario
10001/permisos 0600. No hay endpoint web para importar contraseñas o tokens.

## Docker

```sh
docker build -t garmin-wellness-mcp .
docker volume create garmin-wellness-tokens
docker run --rm -it -v garmin-wellness-tokens:/data/garmin garmin-wellness-mcp garmin-mcp-auth --token-path /data/garmin
docker run --rm --name garmin-wellness -p 127.0.0.1:8000:8000 --env-file .env -e PORT=8000 -e GARMINTOKENS=/data/garmin -v garmin-wellness-tokens:/data/garmin garmin-wellness-mcp
```

La imagen usa Python y uv con digest fijo, instalación `--frozen --no-dev`, y
contexto Docker limitado a código/lock/documentación necesaria. El entrypoint
prepara el volume como root y después abandona esos privilegios antes de arrancar
el servidor. Los secretos no se copian a las capas. `PORT` tiene prioridad sobre
`GARMIN_MCP_PORT`; en Railway escucha `0.0.0.0:$PORT`.

## Railway: redeploy y configuración

El servicio se despliega subiendo el código con CLI. No depende de `railway.toml`
ni del sistema de configuración como código en retirada.

```sh
railway whoami
railway link --project 37e7799e-4bdd-4b3d-a6dc-0a635c1eb261 --environment production --service garmin-mcp
railway up --service garmin-mcp --environment production --detach
railway status
railway logs --service garmin-mcp --lines 80
```

En Railway → servicio `garmin-mcp`:

1. Mantenga el Volume en `/data/garmin` y `GARMINTOKENS=/data/garmin`.
2. En Variables copie [.env.example](.env.example), complete Auth0 y conecte
   `DATABASE_URL` mediante la referencia privada `${{Postgres.DATABASE_URL}}`.
   Mantenga `GARMIN_SYNC_ENABLED=true`. No agregue `GARMIN_PASSWORD`.
3. En Settings → Deploy configure el healthcheck `/healthz` y una sola réplica.
4. Mantenga el dominio público indicado arriba. Un nuevo dominio requiere
   actualizar resource/audience en el servicio y API Auth0.

Compruebe siempre fuera de Railway, además del estado del deployment:

```sh
curl -i https://garmin-mcp-production-fe35.up.railway.app/healthz
curl -i -X POST https://garmin-mcp-production-fe35.up.railway.app/mcp
curl -fsS https://garmin-mcp-production-fe35.up.railway.app/.well-known/oauth-protected-resource
```

Se espera 200, 401 con `WWW-Authenticate`, y metadata con scope `garmin:read`.
El comando de arranque `garmin-wellness` aplica migraciones Alembic antes de servir
cuando `DATABASE_URL` está definido. Si una migración falla, el servidor no inicia.

## PostgreSQL, cache y sync

SQLAlchemy/Alembic mantienen `profiles`, `daily_health`, `sleep`, `hrv`,
`body_battery`, `training`, `activities`, `activities_records`, `sync_state` y `sync_jobs`.
Las mediciones se guardan como proyecciones JSON normalizadas por perfil/fecha;
actividades tienen ID propio. No se persisten respuestas completas, GPS o email.
SQLite sirve para desarrollo y tests; PostgreSQL es el almacenamiento de producción.

El sync usa upserts y un advisory lock PostgreSQL contra ejecuciones simultáneas.
Backfill inicial: 90 días. Después refresca los últimos tres, con un intervalo
mínimo de una hora. El cache reciente tiene TTL configurable (900 segundos por
defecto); histórico completo se reutiliza. La respuesta distingue datos ausentes
de errores de origen. Un refresh parcial conserva mediciones válidas y su antigüedad.

Después de verificar tokens, para una importación manual:

```sh
railway ssh --service garmin-mcp --environment production -- /app/.venv/bin/python /app/container-entrypoint.py garmin-sync --days 90
```

El informe JSON incluye fechas, días solicitados/exitosos/faltantes, errores y días
no intentados. No interprete un proceso terminado como 90 días completos: revise
esos campos. No cree un segundo cron si ya usa el sync de fondo. Las llamadas
interactivas tienen un máximo de 60 lecturas no cacheadas; para rangos largos use
el histórico importado. Garmin tiene máximo tres intentos y backoff; `Retry-After`
se respeta sin mantener una llamada MCP abierta indefinidamente.

### Historial completo y reanudable

`GARMIN_HISTORY_START_DATE=YYYY-MM-DD` habilita la importación histórica desde
una fecha inclusiva, además del refresco reciente. Recorre todas las fechas sin
el límite de 365 días de las consultas interactivas y pagina las actividades
hasta agotar la respuesta. La primera actividad no demuestra por sí sola el
inicio de los datos de salud; verifique también la cobertura previa.

Cada fecha y página completada conserva su cursor en PostgreSQL. Un reinicio
reanuda el trabajo; un error de origen conserva el cursor para reintentar y no se
declara como dato ausente. Las fechas sin mediciones siguen siendo huecos. El
trabajo respeta el mismo bloqueo de sincronización y la autenticación de sólo
lectura. `GARMIN_HISTORY_BATCH_DAYS`, `GARMIN_HISTORY_BATCH_SECONDS` y las pausas
permiten repartir la carga sin bloquear el servidor MCP.

Para consultar progreso sin llamar a Garmin:

```sh
garmin-sync --history-status
```

Para ejecutar un lote manual con los mismos checkpoints:

```sh
garmin-sync --history-start YYYY-MM-DD --batch-days 30
```

`get_history_overview` permite consultar desde ChatGPT la cobertura importada,
el progreso y las medias mensuales de las métricas disponibles. Usa únicamente
PostgreSQL; no lanza miles de llamadas a Garmin desde una consulta interactiva.

## Las 23 herramientas Wellness

| Grupo | Herramientas |
| --- | --- |
| Perfil | `get_profile`, `get_capabilities` |
| Resumen | `get_wellness_today`, `get_daily_health`, `get_health_range` |
| Sueño | `get_sleep`, `get_sleep_analysis`, `get_naps` |
| Recuperación | `get_recovery_context`, `get_hrv`, `get_body_battery`, `get_stress` |
| Entrenamiento | `get_training_overview`, `get_training_readiness`, `get_training_status`, `get_vo2max` |
| Actividades | `get_activities`, `get_activity` |
| Analytics | `get_history_overview`, `get_metric_trend`, `compare_periods`, `get_metric_timeseries`, `find_correlations` |

Todas declaran readOnly, no destructivas, mundo cerrado, scope OAuth y salida
estructurada. `get_profile` no requiere argumentos; usa un ID aleatorio persistido
en el volume y metadata `openai/profile=true`.

Fechas ISO `YYYY-MM-DD`, timezone de la cuenta cuando está disponible y fallback
`GARMIN_TIMEZONE=America/Santiago`. Unidades: segundos, metros, bpm, ms y °C según
métrica. Las series son diarias, máximo 365 días/2000 puntos; resúmenes completos,
31 días. `get_history_overview` consulta sólo cache, hasta 400 meses, y admite hasta 13 métricas; por defecto resume sueño, HRV y pulso en reposo. Recovery usa mediana; comparaciones incluyen n y días faltantes;
correlaciones no implican causalidad.

El [contrato de métricas](docs/wellness-metrics.md) detalla campos, métodos SDK,
estadística y limitaciones. Intraday, GPS, recomendaciones de horario óptimo,
Fitness Age, Endurance/Hill Score y Lactate Threshold no están expuestos en este
perfil. Un dispositivo sin una métrica devuelve disponibilidad explícita, sin
inventar valores. `get_capabilities` informa observaciones, no soporte garantizado.

## CÓMO CONECTARLO A CHATGPT

Según la [guía oficial vigente de OpenAI](https://developers.openai.com/plugins/deploy/connect-chatgpt),
consultada el 2026-10-03:

1. En ChatGPT abra **Settings → Security and login → Developer mode** y actívelo.
   La disponibilidad depende de su cuenta y políticas del workspace.
2. Abra [ChatGPT Plugins](https://chatgpt.com/plugins), pulse **+** y escriba
   `Garmin Wellness` con una descripción de sus consultas de lectura.
3. En **Connection**, elija endpoint público y pegue exactamente:

   ```text
   https://garmin-mcp-production-fe35.up.railway.app/mcp
   ```

4. Seleccione OAuth con cliente predefinido. Client ID:
   `VkenrY7HX6qT3kIkaCKRWxc2lgzEh4mB`. Copie el **Client Secret** directamente desde
   Auth0 al campo seguro de ChatGPT, nunca al chat o a Railway. Revise el callback
   indicado en la conexión; para el tenant actual es
   `https://chatgpt.com/connector_platform_oauth_redirect`.
5. Cree la conexión, inicie sesión en Auth0 como el propietario autorizado y
   acepte `garmin:read`. Revise que aparezcan las 23 herramientas.
6. Abra una conversación nueva y añada la conexión desde el menú de herramientas.
   Tras cambiar herramientas o metadata, abra la conexión y use **Refresh**.

El login Garmin se completa por separado. La conexión OAuth puede funcionar y aun
así faltar una sesión Garmin; en ese caso las herramientas informarán datos no
disponibles hasta completar el bootstrap. Más detalles: [Auth0](docs/auth0.md).

## Troubleshooting

| Síntoma | Acción |
| --- | --- |
| Garmin token expired / authentication_required | Ejecute `garmin-mcp-auth --verify` en el volume; si falla, repita login interactivo y MFA |
| MFA sin terminal | Use una terminal privada interactiva con Railway SSH; no envíe MFA por chat |
| MCP 401 | Revise JWT, issuer con `/` final, expiry, audience exacto y configuración OAuth; no desactive auth |
| MCP 403 | Revise `garmin:read` y que User ID Auth0 coincida con `AUTH0_ALLOWED_SUBJECT` |
| Audience mismatch | API Identifier = `AUTH0_AUDIENCE` = `MCP_RESOURCE_URL`; active Resource Parameter Compatibility en Auth0 |
| Client not authorized to access resource server | API Auth0 → Application Access → aplicación ChatGPT → acceso delegado `garmin:read` |
| OAuth login_required | Normal en una prueba sin sesión; complete login/consentimiento interactivo |
| Railway PORT / healthcheck failure | Revise binding `0.0.0.0:$PORT`, migraciones, logs y healthcheck `/healthz` |
| No reviewed Wellness tools registered | Código o SDK no coincide con el manifiesto auditado; revise implementación y fingerprints antes de redeployar |
| Garmin timeout | Reduzca rango; consulte histórico cacheado; revise timeout y conectividad |
| Rate limiting | Espere el cooldown/Retry-After; no lance syncs repetidos |
| Unsupported metric / null | Compruebe reloj, fecha y `get_capabilities`; ausencia no significa cero |
| SSH permission denied | Registre su clave pública en Railway y verifique proyecto/servicio; nunca transfiera la clave privada |

Consultas de ejemplo:

- ¿Cómo dormí anoche?
- Dame un resumen de mi estado de recuperación de hoy.
- Compara mi HRV de los últimos 7 días con los 30 anteriores.
- ¿Cómo ha evolucionado mi frecuencia cardíaca en reposo este mes?
- Compara mi sueño y Training Readiness.
- Muéstrame mis últimas actividades.
- Busca tendencias entre sueño, estrés y HRV durante los últimos 90 días.

## Upstream y licencia

Basado en [Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp), preservando
su [licencia MIT](LICENSE) y atribución. La integración Garmin utiliza
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect).
El [README upstream original](docs/upstream-readme.md) se conserva como referencia
histórica; sus herramientas mutadoras y antiguas instrucciones HTTP no describen
el deployment Wellness protegido.

```sh
git remote add upstream https://github.com/Taxuspt/garmin_mcp.git  # sólo si falta
git fetch upstream
```

Integre futuras actualizaciones en una rama separada, revise dependencias y
clasificaciones de lectura, regenere el lock y ejecute tests antes de desplegar.
