# Auditoría técnica de StreamVault

La revisión partió de `a1e985ecbd964a26584a868564d88932fc4c338a`. Los cambios
`146e9a7`, `09fb446` y `a1e985e` ya aportaban entrega local por nginx,
mantenimiento de caché, sincronización de subtítulos, detección de HEVC y
recuperación de la posición. Son el punto de partida de esta auditoría.

## Flujos revisados

| Flujo | Entrada y dependencias |
| --- | --- |
| Reproducción local | [Ficha](../vault/assets/js/media_detail.js) → [motor de cliente](../vault/assets/js/local-player.js) → [descriptor, remux y subtítulos](../vault/core/routers/downloads.py) → [archivo autenticado](../vault/core/services/media_response.py) o FFmpeg. [nginx](../vault/nginx.conf) puede entregar los originales compatibles. |
| Fuentes remotas y TV | [Resolver](../vault/core/routers/resolver.py) → [proveedores y embeds](../vault/core/providers/) o [catálogo local y proxy](../vault/core/routers/media_local.py) → [proxy HLS](../vault/core/services/hls_proxy.py). [IPTV](../vault/core/routers/iptv.py) también usa el proxy HLS. |
| Federación | [Catálogo, permisos y proxy](../vault/core/routers/federation.py) → [servicio de nodos](../vault/core/services/federation_service.py) → descriptor y reproducción del servidor propietario. |
| Descargas | [API web](../vault/core/routers/downloads.py) → [contenedor downloader](../vault/core/services/downloader_app.py) → [yt-dlp](../vault/core/services/download_service.py) o [aria2](../vault/core/services/torrent_service.py) → marca de trabajo parcial y publicación de completados. |
| Biblioteca y metadatos | [Escaneo, identificación y recuperación](../vault/core/services/media_library.py) → [TMDB](../vault/core/services/tmdb_service.py) y [SQLite](../vault/core/database.py). [Subtítulos](../vault/core/services/subtitles_service.py) son una consulta independiente. |
| Almacenamiento | [Presupuestos](../vault/core/services/storage_policy.py), [caché y leases](../vault/core/services/storage_maintenance.py), [explorador](../vault/core/services/admin_download_library.py) y [backups](../vault/core/services/updater_service.py). |
| Offline y Android | [Almacenamiento offline](../vault/assets/js/offline-store.js), [service worker](../vault/assets/sw.js) y [aplicación Android](../android/app/). |
| Operación y actualizaciones | [Arranque y cierre](../vault/core/main.py) → [Compose](../vault/docker-compose.yaml), [proxy](../vault/nginx.conf), [actualizador](../vault/core/services/updater_service.py) y [releases](RELEASES.md). |

## Hallazgos por prioridad

P0 puede afectar una reproducción activa o la integridad del almacenamiento.
P1 afecta disponibilidad, seguridad, rendimiento sostenido o recuperación.
P2 requiere cambios planificados y P3 recoge deuda de mantenimiento.

| Prioridad · estado | Causa e impacto | Solución y verificación |
| --- | --- | --- |
| P0 · corregido | [Respuesta de streaming](../vault/core/services/streaming_response.py#L26) delegaba la desconexión en el envío del siguiente bloque con ASGI 2.4. Una lectura bloqueada de FFmpeg o de un nodo podía retener un proceso o una conexión. | Escuchar `http.disconnect` mientras fluye el cuerpo y ejecutar el cierre una vez. [Regresión](../vault/core/tests/test_download_playback.py#L417) con desconexión mientras el primer bloque sigue pendiente. |
| P0 · corregido | [Mantenimiento](../vault/core/services/storage_maintenance.py#L53) solo registraba leases en memoria. Un worker podía eliminar una copia usada por otro. Al preparar la reproducción, [el pin de caché](../vault/core/services/storage_maintenance.py#L92) seguía enlaces simbólicos. | Locks compartidos durante lectura o escritura y exclusivos al comprobar y borrar, sobre el mismo volumen. [Regresiones](../vault/core/tests/test_storage_maintenance.py) para enlaces a originales, protección durante el uso, muerte del worker, Range y ETag. |
| P1 · corregido | [Ficha](../vault/assets/js/media_detail.js#L380) esperaba la resolución federada antes de mostrar un archivo del propio servidor. | Publicar en cuanto responde la búsqueda local y conservar la selección al añadir alternativas. [Regresión Chromium](../vault/core/tests/test_federation_player_browser.py#L10) con federación detenida. |
| P1 · corregido | [Remux](../vault/core/routers/downloads.py#L967) cancelaba toda la cola al iniciar una reproducción y podía descartar trabajo para otros archivos. | Interrumpir solo la conversión activa y conservar tareas pendientes hasta que cierre la última reproducción. [Regresión](../vault/core/tests/test_download_playback.py#L474) con un trabajo activo y otro en cola. |
| P1 · pendiente | [Presupuesto de disco](../vault/core/services/storage_policy.py) comprueba espacio sin reservarlo. Dos escritores pueden aprobar los mismos bytes antes de empezar a escribir. | Reserva compartida con liberación al finalizar y reconciliación. Probar dos procesos y recuperación tras caída con disco limitado. |
| P1 · parcial | [TMDB](../vault/core/services/tmdb_service.py#L34) permitía superar `_CACHE_MAX` con entradas frescas. Algunos flujos de descubrimiento dependen de respuestas externas. | LRU en memoria con un límite de 500 entradas y acceso coordinado entre hilos. [Regresión](../vault/core/tests/test_tmdb_pagination.py) con entradas frescas y caducidad. Pendientes persistencia, límite de bytes y pruebas de 429 y desconexión. |
| P1 · pendiente | [yt-dlp de descargas](../vault/core/services/download_service.py) y [YouTube](../vault/core/services/youtube_service.py) deshabilitan la comprobación de certificados. | Activar la política TLS predeterminada y probar certificado válido y certificado no confiable. |
| P1 · pendiente | [Federación](../vault/core/services/federation_service.py) crea un cliente HTTP por solicitud de nodo. Seek y cambios de pista repiten conexiones, además de [verificar el destino](../vault/core/services/federation_service.py). | Gestionar pools con vida útil acotada por origen, identidad TLS y destino validado. Probar reutilización, redirecciones, desconexión y revocación. |
| P1 · pendiente | [Descargas HTTP](../vault/core/services/download_service.py) y [torrent](../vault/core/services/torrent_service.py) mantienen el estado operativo en memoria. La recuperación de [marcas parciales](../vault/core/services/media_library.py) no equivale a recuperar la cola. | Persistir transiciones y reconciliar archivos y procesos. Probar reinicio en cada estado y cancelación del worker real. |
| P2 · pendiente | [Biblioteca](../vault/core/services/media_library.py) recorre directorios cuando invalida el índice en memoria. [TMDB](../vault/core/services/tmdb_service.py) puede aceptar el primer resultado de búsqueda. | Índice incremental SQLite y selección con confianza, identidad de episodio y correcciones persistentes. Probar grandes bibliotecas, temporadas, títulos ambiguos e idiomas mixtos. |
| P2 · pendiente | [Arranque](../vault/core/main.py) usa manejadores depreciados y las tareas periódicas viven por proceso. [SQLite](../vault/core/database.py) ya configura WAL y espera de bloqueo, pero aún hay que medir el coste de transacciones y consultas. | Migrar el ciclo de vida, coordinar tareas y verificar migraciones, cierre y backups consistentes. |
| P2 · pendiente | [Compose](../vault/docker-compose.yaml) usa una referencia flotante para nginx. El [actualizador](../vault/core/services/updater_service.py) requiere operaciones privilegiadas de despliegue. | Fijar imágenes comprobadas, delimitar permisos del actualizador y distinguir readiness de liveness. Probar instalación y actualización con el directorio de datos existente. |
| P2 · pendiente | [Interfaz](../vault/assets/js/media_detail.js) concentra carga de fuentes, metadatos, progreso y ciclo de reproducción. El benchmark registra unos 2780 nodos en el portal de prueba. | Medir y dividir por responsabilidades. Evaluar renderizado progresivo y carga de imágenes con los tres tamaños de viewport y navegación por mando. |
| P3 · comprobación pendiente | [Transcoder histórico](../vault/core/services/transcoder.py) genera un manifiesto que referencia MP4 completos. La búsqueda de referencias en el árbol versionado encontró su reexportación, sin rutas que lo instancien. | Verificar instalaciones que importen ese servicio antes de retirarlo. Si se habilita HLS adaptativo, exigir manifiestos y segmentos válidos y reproducción real. |

## Línea base reproducible

Usar Python 3.11 y las dependencias de [la suite](VERIFICATION.md), Chromium de
Playwright, nginx y el bundle local de Hls.js. Comparar con el mismo checkout
archivado, navegador, host, configuración y carga de fondo:

```sh
baseline_dir=$(mktemp -d)
git archive a1e985e | tar -x -C "$baseline_dir"
python vault/scripts/performance.py --root "$baseline_dir" \
  --nginx "$(command -v nginx)" --vendor /ruta/bundles \
  --routes home detail local_detail --runs 3 --label a1e985e \
  --output /tmp/streamvault-before.json
python vault/scripts/performance.py --nginx "$(command -v nginx)" \
  --vendor /ruta/bundles --routes local_detail --runs 3 --label local-first \
  --output /tmp/streamvault-after.json
```

Se midieron medianas de tres navegaciones frías con 150 ms de latencia,
1,6 Mb/s de descarga, CPU ralentizada 4×, API sintética, nginx y sin carteles
ni fondos. El escenario `local_detail` añade 1500 ms de retraso en el nodo
y mide hasta que el selector ofrece el archivo de este servidor.

| Vista | LCP portal | LCP ficha | CLS ficha | Fuente local inicial | Fuente local tras el cambio |
| --- | ---: | ---: | ---: | ---: | ---: |
| Móvil | 1,772 s | 1,180 s | 0,02 | 3,463 s | 2,187 s |
| Escritorio | 1,784 s | 1,216 s | 0,01 | 3,777 s | 2,381 s |
| TV | 1,880 s | 1,328 s | 0,00 | 3,733 s | 2,240 s |

La disponibilidad de la fuente mide el selector. Para medir Direct Play y
remux hacen falta un archivo real y el tiempo a primer fotograma. Quedan
también por medir INP, CPU, memoria, ancho de banda con medios reales y
reproducción prolongada. El host también ejecutaba pruebas, así que estas
medianas respaldan la regresión de la espera federada y tienen variación de
carga ajena al escenario. Los resultados no se extrapolan a dispositivos reales.

## Validación de esta fase

- Línea base en Python 3.11: 464 pruebas unitarias pasaron. La validación
  estática pasó en 154 módulos Python, 21 plantillas y 30 recursos JavaScript.
- Tras los cambios pasaron las 469 pruebas de `check.py unit -q` y
  `check.py validate`.
- Antes de corregirlos fallaron los casos de desconexión bloqueada, lease
  entre procesos, cancelación de la cola, acceso por un enlace simbólico y
  caché de TMDB desbordada. El caso de fuente local falló en los tres perfiles.
- Pasaron 65 pruebas dirigidas de reproducción, almacenamiento, metadatos y
  federación. También pasaron 18 de reproducción federada y 3 de controles
  con vídeo sintético en Chromium.
- La pasada `baseline` obtuvo 159 resultados correctos y 3 errores de
  conexión con el driver de Playwright. Los tres casos pasaron al repetirlos
  de forma aislada usando el Node.js del sistema.
- Los 475 casos de `browser` pasaron repartidos por módulos y perfiles,
  con reintentos aislados. Las pasadas largas sufrieron cierres intermitentes
  del driver y bloqueos de la suite. La incidencia del driver también apareció
  en el commit de referencia.
- Android pasó las 24 pruebas de `testDebugUnitTest` con el SDK local y
  Gradle en modo offline.
- `check.py build` completó la validación estática y quedó bloqueado por
  la ausencia de Docker con el plugin de Compose y un daemon.
