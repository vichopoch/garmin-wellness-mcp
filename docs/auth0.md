# OAuth del Garmin Wellness MCP

El servicio es un **resource server de un solo propietario**. Auth0 realiza login,
consentimiento, authorization code, PKCE S256 y emisión/renovación de tokens.
El servidor valida cada petición antes de permitir acceso a los datos Garmin:
firma RS256, issuer, audience, expiración, nbf si existe, `garmin:read` y el `sub`
exacto del propietario. No recibe la contraseña Auth0 ni requiere client secret.
Los tokens Garmin y los access tokens MCP son credenciales diferentes.

## Configuración necesaria

Use el dominio HTTPS real del servicio, sin `/` final después de `/mcp`:

```dotenv
AUTH_MODE=oauth
AUTH0_DOMAIN=dev-kcplgn5234ipl76y.us.auth0.com
AUTH0_ISSUER=https://dev-kcplgn5234ipl76y.us.auth0.com/
AUTH0_JWKS_URL=https://dev-kcplgn5234ipl76y.us.auth0.com/.well-known/jwks.json
AUTH0_AUDIENCE=https://garmin-mcp-production-fe35.up.railway.app/mcp
MCP_RESOURCE_URL=https://garmin-mcp-production-fe35.up.railway.app/mcp
AUTH0_ALLOWED_SUBJECT=auth0|YOUR-OWNER-USER-ID
```

El audience debe coincidir **exactamente** con el resource, incluido `/mcp`.
`AUTH0_ALLOWED_SUBJECT` es el User ID de Auth0, nunca el email. No se acepta una
lista de usuarios: todos los datos pertenecen a una única cuenta Garmin.
El issuer conserva su `/` final. JWKS e issuer deben tener el mismo dominio HTTPS.

Si falta cualquier dato, se mantiene `/healthz` disponible, `/mcp` rechaza con
401 y el discovery devuelve 503. `AUTH_MODE=none` también bloquea el acceso.
No existe un bypass remoto de autenticación.

## Acciones humanas en Auth0 y ChatGPT

1. Entre en [Auth0 Dashboard](https://manage.auth0.com/) y seleccione un tenant
   dedicado a este servicio. En **Applications → APIs → Create API**, cree
   `Garmin Wellness`, identificador igual a `MCP_RESOURCE_URL`, perfil Auth0 y
   algoritmo RS256. En **Permissions**, añada únicamente `garmin:read`.
2. En **Settings → Advanced → Settings**, active **Resource Parameter
   Compatibility Profile** para traducir el parámetro OAuth `resource` al audience
   de la API. [Instrucciones oficiales de Auth0](https://support.auth0.com/center/s/article/mcp-audience-error-with-auth0).
3. En **Applications → Applications → Create Application**, cree `ChatGPT Garmin
   Wellness` de tipo **Regular Web Applications**. En **Settings → Allowed Callback
   URLs**, introduzca `https://chatgpt.com/connector_platform_oauth_redirect`.
   El discovery de este tenant anuncia `authorization_response_iss_parameter_supported:
   true`, por lo que corresponde el callback estable. Si la página de gestión de
   ChatGPT muestra otro callback, copie exactamente el mostrado, sin comodines.
   En **Advanced Settings → Grant Types**, habilite **Authorization Code**;
   para reconexión automática, también **Refresh Token** y **Allow Offline Access**
   en la API. ChatGPT ejecuta PKCE S256 en el flujo de código.
4. En **Credentials**, configure autenticación del token endpoint mediante
   **Client Secret (Post)** (`client_secret_post`). Copie **Client ID** y **Client
   Secret** directamente a los campos OAuth de la nueva conexión MCP de ChatGPT.
   No envíe el secret al asistente ni lo guarde en Railway: sólo el cliente ChatGPT
   lo necesita. En **Connections**, habilite la conexión de su usuario propietario.
   En **Applications → APIs → Garmin Wellness → Settings → Application Access
   Policy**, seleccione **User-Delegated Access: Per-app authorization** y guarde.
   Después vaya a **Application Access → su aplicación ChatGPT → Edit → Grant
   Access (User-Delegated Access)**, seleccione `garmin:read` y guarde. No conceda
   Client Access/M2M. Esto corrige `Client not authorized to access resource server`.
5. En **User Management → Users**, cree o seleccione su propia identidad y copie
   **User ID** a `AUTH0_ALLOWED_SUBJECT` en Railway → servicio → Variables.
   Si activa RBAC en la API, asigne `garmin:read` a un rol del propietario.
6. Copie dominio, issuer, JWKS y audience a las variables anteriores en Railway.
   Son configuración pública/identificadores; no publique access tokens,
   contraseñas ni refresh tokens. Reinicie el servicio.
7. Complete el login y consentimiento en ChatGPT con esa misma identidad.

La creación del cliente utiliza [Regular Web Applications de Auth0](https://auth0.com/docs/get-started/auth0-overview/create-applications/regular-web-apps)
y sus [Application Settings](https://auth0.com/docs/get-started/applications/application-settings).
El cliente predefinido evita habilitar DCR globalmente. CIMD es una alternativa:
si se elige, active CIMD en Settings → Advanced e importe la URL exacta mostrada
por ChatGPT en Applications → Create Application → Import from URL, siguiendo
la [guía oficial de OpenAI](https://github.com/openai/openai-mcpkit/blob/main/python-authenticated-mcp-server-scaffold/README.md#2-configure-auth0-authentication).
No configure ambos métodos para la misma conexión.

La [documentación de autenticación de OpenAI](https://developers.openai.com/plugins/build/auth)
exige discovery de PKCE S256 y `resource`; admite CIMD, DCR o cliente OAuth
predefinido. Para una conexión predefinida, utilice el callback mostrado por la
página de gestión de esa conexión, nunca un callback supuesto. Puede ser específico
de la conexión; la URI estable depende de que el proveedor anuncie y cumpla RFC
9207. No implemente un proxy OAuth improvisado para corregir discovery.

## Verificación

```sh
curl -i https://garmin-mcp-production-fe35.up.railway.app/healthz
curl -i -X POST https://garmin-mcp-production-fe35.up.railway.app/mcp
curl -fsS https://garmin-mcp-production-fe35.up.railway.app/.well-known/oauth-protected-resource
curl -fsS https://dev-kcplgn5234ipl76y.us.auth0.com/.well-known/openid-configuration
```

Resultados esperados: health 200; MCP 401 con `WWW-Authenticate` y URL de metadata;
metadata 200 con resource exacto, issuer exacto y `garmin:read`. Discovery Auth0 debe
anunciar `code_challenge_methods_supported` incluyendo `S256`, endpoints válidos
y el método de autenticación del cliente configurado. La metadata del recurso
está también disponible en `/.well-known/oauth-protected-resource/mcp`.

Compruebe después el flujo interactivo real con ChatGPT o MCP Inspector. Un test
JWT local verifica el resource server, pero no demuestra que el tenant, PKCE,
callbacks o consentimiento reales estén configurados correctamente. No pegue tokens
en argumentos shell, capturas o mensajes; deje que el cliente OAuth los gestione.

Un 401 con token exige revisar firma, issuer, audience y expiración. Un 403 exige
revisar scope y User ID permitido. Un usuario distinto se rechaza aunque su token
sea válido y contenga `garmin:read`.

### Login correcto, descubrimiento de herramientas fallido

Consulte el evento privado `auth` en Railway. `access_denied` significa que el
`sub` no coincide con `AUTH0_ALLOWED_SUBJECT`; `insufficient_scope` significa que
el usuario ya coincidió, pero el token no contiene `garmin:read`. No desactive
ninguna de las dos comprobaciones para resolver la conexión.

Entrar al panel de administración de Auth0 con Google no determina la identidad
usada por la aplicación. Un login Google y otro de Username-Password-Authentication
pueden tener User ID diferentes aunque compartan email. En la pantalla abierta
por ChatGPT, use la identidad exacta configurada como propietario. Si sólo se
usará la conexión de email/contraseña, deshabilite Google únicamente en las
Connections de esta aplicación. Cierre la sesión del tenant antes de reconectar
para evitar reutilizar otra identidad.

Si falta el scope, confirme primero que `garmin:read` existe en **Applications
→ APIs → Garmin Wellness → Permissions**; añádalo allí si no aparece. Compruebe
después el grant **User-Delegated Access** del cliente. Con RBAC desactivado no
es necesario asignar el permiso al usuario. Con RBAC activo, Auth0 incluye en `scope` la
intersección de permisos solicitados y asignados al usuario. En **User Management
→ Users → propietario → Permissions → Assign Permissions**, asigne `garmin:read`
de la API Garmin Wellness, o use un rol que lo contenga. El grant del cliente por
sí solo no sustituye esta asignación. Después desconecte y vuelva a conectar en
ChatGPT para obtener un token nuevo con el permiso solicitado y consentido.
Véanse [RBAC en Auth0](https://auth0.com/docs/get-started/apis/enable-role-based-access-control-for-apis)
y [asignación de permisos](https://auth0.com/docs/manage-users/access-control/configure-core-rbac/rbac-users/assign-permissions-to-users).

## Privacidad de logs

`configure_private_logging()` emite JSON sólo con evento, nivel, herramienta,
duración, estado, request ID generado por el servidor y tipo de error. Descarta
mensajes arbitrarios, tracebacks, argumentos, payloads, resultados y campos no
permitidos; así Authorization, cookies, email, password, MFA, tokens y métricas no
entran en los logs. Configure Uvicorn con `log_config=None, access_log=False`.
`LOG_SENSITIVE_DATA=true` no habilita logging sensible en producción.

Las claves JWKS se cachean cinco minutos; un `kid` nuevo fuerza actualización,
con un máximo de una descarga cada 30 segundos y timeout de cinco segundos.
Durante una rotación recién ocurrida puede necesitar reintentar después de ese
intervalo. Las claves y URLs indicadas por el propio token nunca sustituyen al
JWKS configurado.
