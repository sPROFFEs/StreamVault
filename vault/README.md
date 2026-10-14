# StreamVault Deployment Guide

Aplicacion FastAPI para un centro multimedia personal.

La guía vigente de instalación, uso en escritorio/móvil/TV y compilación está en
[Setup](../docs/SETUP.md). El contrato de versiones, APK firmado, automatización
GitHub y actualización interna está en [Releases](../docs/RELEASES.md).

## Que funciona ahora

- autenticacion basica
- portal de entrada
- catalogo apoyado en TMDB si defines `TMDB_API_KEY`
- TV en vivo con listas M3U publicas
- rutas para YouTube y descargas ya presentes en el backend

## Límites operativos

- la calidad y estabilidad de resolvers y canales externos depende de terceros
- los subtítulos gráficos incluidos en algunos MKV no se pueden convertir en el navegador; las pistas de texto sí se extraen a WebVTT
- una revisión automatizada no sustituye una prueba de penetración profesional antes de exponer una biblioteca sensible

## Requisitos

- Docker y Docker Compose
- una clave de TMDB si quieres catalogo util
- un directorio persistente para datos en el host

## Puesta en marcha

1. Copia `.env.example` a `.env`.
2. Rellena `SECRET_KEY`, `DOMAIN` y `TMDB_API_KEY`.
3. Ajusta `HOST_DATA_ROOT`; el ejemplo manual usa `./data` y el instalador usa `/opt/saas-data`.
4. Lanza `docker compose up -d --build`.

## One-command install

En una máquina Debian o Ubuntu limpia, clona el repositorio y ejecuta el instalador no interactivo. Instala Docker si hace falta, genera el secreto de sesión, levanta los servicios, espera al health check y crea el primer administrador:

```bash
sudo apt-get update && sudo apt-get install -y git && git clone <repository-url> /opt/streamvault && sudo /opt/streamvault/vault/setup.sh --non-interactive --domain localhost
```

También se puede iniciar directamente desde la copia `raw` del script; `--repo-url` hace que el propio instalador clone el resto del proyecto:

```bash
curl -fsSL <raw-setup-script-url> | sudo bash -s -- --non-interactive --repo-url <repository-url> --domain localhost
```

El comando genera una contraseña segura y la muestra una sola vez al terminar. Para automatización, pasa las credenciales sin escribir la contraseña como argumento:

```bash
sudo env STREAMVAULT_ADMIN_USERNAME=owner STREAMVAULT_ADMIN_PASSWORD='ChangeMe9!' /opt/streamvault/vault/setup.sh --non-interactive --domain watch.example.com
```

La contraseña debe contener mayúsculas, minúsculas, números y símbolos. Se puede volver a ejecutar el instalador con seguridad: actualiza el administrador indicado sin borrar la biblioteca ni recrear la base de datos.

## Variables importantes

| Variable | Uso |
| :--- | :--- |
| `SECRET_KEY` | Firma de tokens |
| `DOMAIN` | Dominio base o `localhost` |
| `ALLOWED_HOSTS` | Hosts permitidos |
| `TMDB_API_KEY` | Metadatos del catalogo |
| `HOST_DATA_ROOT` | Persistencia en host |
| `CONCURRENT_DOWNLOADS` | Descargas simultáneas; por defecto `1` para proteger reproducción y túnel |
| `DOWNLOAD_RATE_LIMIT` | Límite total de descarga en bytes/s; por defecto `4194304` (4 MiB/s) |
| `TORRENT_UPLOAD_RATE_LIMIT` | Límite total de subida torrent en bytes/s; por defecto `65536` (64 KiB/s) |
| `DOWNLOADER_CPUS` | CPU máxima para descargas; por defecto `1.0`, con prioridad inferior a core |
| `DOWNLOADER_MEMORY_LIMIT` | Memoria máxima de descargas; por defecto `768m` |
| `MAX_LIVE_REMUXES` | Reproducciones simultáneas que necesitan FFmpeg; por defecto `2` |
| `TUNNEL_MEMORY_LIMIT` | Memoria máxima del contenedor del túnel; por defecto `512m` |
| `TUNNEL_TOKEN` | Token de Cloudflare Tunnel (opcional, para arranque directo desde `.env`) |
| `TUNNEL_HOSTNAME` | Hostname público de Cloudflare (ej. `watch.example.com`) |

## Estrategia de fuentes

- Catalogo: TMDB.
- TV abierta: listas publicas de IPTV-org por pais, region y categoria.
- Subtitulos: pendiente de integracion especifica.

## Stack actual

- `core`: FastAPI con SQLite en `/saas-data`, catálogo, interfaz y streaming.
- `downloader`: servicio aislado para descargas pesadas (`aria2c`, `yt-dlp`), liberando a core de saturación de CPU, sockets e I/O.
- `cloudflared`: conector independiente con API HTTP interna (puerto 8080), aprovisionamiento, reinicio automático y estado de salud propio.
- `nginx`: proxy frontal y entrega de assets.

## Migrar el túnel a su propio contenedor

Desde la raíz del repositorio:

```bash
git pull --ff-only
docker compose -f vault/docker-compose.yaml up -d --build --force-recreate core nginx cloudflared
```

Compose activa `CLOUDFLARED_EXTERNAL=true` en core. El servicio `cloudflared` lee
la misma configuración cifrada en `/saas-data/config/cloudflare_tunnel.json`,
usando el mismo `SECRET_KEY`; no hay que volver a introducir el token. Conserva
`HOST_DATA_ROOT` y `SECRET_KEY` al actualizar. El directorio compartido usa el
UID 1000 de `appuser`; el arranque de core prepara sus permisos.

Los botones de Administración siguen funcionando; el servicio independiente
aplica los cambios en unos dos segundos. Detener explícitamente el túnel lo
desconecta, pero reiniciar core ya no lo detiene. Un túnel gestionado conserva
su dominio; un túnel rápido puede cambiar de URL durante esta migración.
El origen debe ser accesible desde el nuevo contenedor, normalmente
`http://nginx:80`; `localhost` dentro de él no apunta a core.

Comprueba el servicio con `docker compose -f vault/docker-compose.yaml ps cloudflared`.
Su configuración y últimos mensajes también aparecen en Administración. Mientras
core se reinicia, el conector permanece activo pero la aplicación no puede servir
peticiones hasta que core vuelva. Los contenedores siguen compartiendo el ancho
de banda y recursos del host: conserva los límites de descarga/subida.

Para ejecutar core sin Compose se mantiene el modo integrado por defecto
(`CLOUDFLARED_EXTERNAL=false`); no actives ambos modos para la misma instalación.

Se eliminaron del despliegue por defecto los servicios muertos que venian de la base heredada. Si algun dia recuperas esa funcionalidad, reintroducela con criterio y pruebas.

## Nota operativa

El acceso remoto debe terminar en HTTPS (por ejemplo, el túnel gestionado desde Administración). No publiques el puerto 8000 directamente en Internet.

## Actualizar los fixes de descargas, túnel y reproducción

Desde la raíz del repositorio, después de actualizar el código:

```bash
docker compose -f vault/docker-compose.yaml up -d --build --force-recreate core downloader cloudflared nginx
```

Esto interrumpe las reproducciones y descargas activas; aplícalo sin transferencias
en curso. No borra la biblioteca. **Reiniciar solo core desde Administración no
actualiza el túnel**: su código está incluido en la imagen y requiere reconstrucción.

Los valores ya definidos en `.env` tienen prioridad sobre los nuevos límites.
Ajusta descarga/subida por debajo del 70% de la capacidad real de tu conexión,
reservando especialmente subida para el vídeo remoto. Los límites usan bytes/s,
no bits/s. Separar contenedores no separa su conexión a Internet.

La reproducción de archivos compatibles conserva HTTP Range y no usa FFmpeg.
Las conversiones en vivo esperan hasta cinco segundos de búfer (ocho segundos
máximo de espera inicial). La caché se prepara después de cerrar la reproducción,
y una nueva conversión en vivo cancela ese trabajo secundario para evitar dos
codificadores compitiendo por recursos. Los formatos que requieren convertir vídeo
siguen necesitando una CPU capaz de codificar en tiempo real.

El estado del túnel comprueba conexiones reales mediante el endpoint `/ready`
de cloudflared, no solo si existe un proceso. Tras 120 segundos sin conexión,
el supervisor reinicia el conector; antes deja actuar su reconexión interna.

Si persiste un error, captura `docker compose -f vault/docker-compose.yaml logs
--since 10m cloudflared downloader core nginx` y `docker stats --no-stream` durante
la incidencia para distinguir saturación del enlace, falta de memoria y fallos del
origen. No compartas tokens ni claves.
