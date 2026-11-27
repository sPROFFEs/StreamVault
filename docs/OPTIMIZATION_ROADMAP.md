# Plan de optimización de StreamVault

La [auditoría](TECHNICAL_AUDIT.md) conserva la referencia inicial, los casos
reproducibles y las mediciones. Cada fase debe poder validarse y revertirse por
separado. Las interfaces existentes, los volúmenes de datos, el actualizador y
el APK siguen siendo la base de la transición.

| Fase | Alcance | Verificación de salida |
| --- | --- | --- |
| 0 · Auditoría | Mapa de flujos, baseline y reproducibilidad. | Comparar el mismo escenario en el commit de referencia y en el cambio. Dejar constancia de pruebas bloqueadas. |
| 1 · Reproducción | Consolidar decisiones de reproducción para que local y federación compartan matriz de códecs, pistas, contenedor, bit depth y HDR. Elegir Direct Play, remux, solo audio o transcodificación. Evaluar QSV, VAAPI y NVENC con fallback por software. HLS bajo demanda si resulta útil. | Matriz H.264, HEVC, AV1 y VP9 con MP4, MKV, WebM y TS. Comprobar Range/HEAD/416, seek, idiomas, offsets y reproducción real en equipos de capacidad limitada. |
| 2 · Trabajos | La primera entrega separa el codificador activo de la cola de remux. Extender la coordinación con límites globales por tipo, single-flight, timeouts y recuperación. Registrar el tipo de fallo y el código de salida sin registrar URL, argumentos ni rutas privadas. | Concurrencia entre varios espectadores, descargas y limpieza. Verificar cancelación del proceso, continuidad de la cola y límites de memoria. |
| 3 · Fuentes | Contrato de fuente para biblioteca propia, nodos autorizados e integraciones permitidas. Incluir identidad, capacidades, idiomas, coste, expiración y disponibilidad. Aislar proveedores con TTL, healthchecks, límites, circuit breaker, backoff con jitter y respeto de Retry-After. | Fuente local disponible con nodos y proveedores bloqueados. Fallback acotado que conserva posición. Pruebas de autorización, deduplicación y preferencias. |
| 4 · Transporte | Gestionar clientes por ciclo de vida, origen y destino validado. Aplicar backpressure, timeouts diferenciados y límites de conexiones. Conservar Range y descarga por nginx. El cierre ante desconexión ya está cubierto en el primer tramo. | Red lenta, apertura/cierre y seek repetidos, desconexión antes del primer byte y concurrencia mientras descarga el servidor. Correlacionar con nginx y el túnel. |
| 5 · Identificación | El segundo tramo incorpora análisis estructurado, coincidencias por título original, localizado, año e ID, y correcciones manuales desde el explorador. Los casos sin referencias fiables quedan en revisión. | Títulos numéricos, variantes Unicode, remakes, temporadas, rangos, especiales y correcciones sin mover archivos. |
| 6 · Índice | El segundo tramo introduce SQLite incremental por ruta y huella de archivo, consultas y paginación local, reconciliación de cambios externos y persistencia de FFprobe. | Archivo agregado, modificado y borrado, reinicio y biblioteca de 5.000 títulos. |
| 7 · TMDB | La tercera entrega incorpora caché SQLite con límite de entradas y bytes, TTL por recurso, actualización en segundo plano y conexiones compartidas. Los archivos con ID registrado conservan su ficha local si faltan metadatos remotos. | Caché saturada, reinicio, ocho lecturas concurrentes, 429 con Retry-After, 5xx, timeout y reproducción local sin credenciales de TMDB. |
| 8 · Disco | El segundo tramo incorpora reservas compartidas, perfiles Minimal, Balanced y Performance, revisión de parciales recuperables y retención opcional de backups. La caché regenerable conserva TTL y LRU. | Dos escritores con presupuesto compartido, liberación tras caída, revisión de parciales y protección de activos. |
| 9 · Descargas | La cuarta entrega persiste la cola SQLite, conserva la prioridad y el progreso, restaura trabajos compatibles y verifica los archivos antes de publicarlos. yt-dlp y aria2 validan TLS. | Pruebas de recuperación del estado y de la publicación, cancelación del grupo de procesos y descargas reales por HTTP y BitTorrent. Una temporada conserva los episodios y los parciales en su directorio hasta verificar el conjunto seleccionado. |
| 10 · Web | Fuentes locales independientes de preferencias y del historial, espera acotada antes del fallback, cancelación de búsquedas y aislamiento entre episodios. El progreso agrupa muestras y la PWA conserva un listado acotado de recursos offline. | Regresiones con una temporada tardía, un nodo detenido y guardado de progreso al cambiar de episodio. Medición del tiempo hasta la primera fuente local. |
| 11 · SQLite | Migraciones aditivas serializadas, índices para historial y federación, menos escrituras repetidas y un worker periódico por volumen. | Migraciones concurrentes sobre una base heredada, comprobación del plan de consulta, sucesión del worker y apagado mediante lifespan. |
| 12 · Operaciones | Manifiestos Docker fijados, límites ajustables, comprobaciones de disponibilidad, limpieza en seco, registros nginx estructurados y métricas ligeras. Se puede retirar el socket Docker al gestionar las actualizaciones desde el host. | Verificar migración, salud con SQLite ocupado, limpieza en seco y arranque de nginx con servicios locales. La validación de imágenes y del túnel requiere Docker. |
| 13 · Validación | Extender las pruebas deterministas y los benchmarks al incorporar cada fase. Mantener CI web, Android y producción. | `check.py unit`, `baseline`, `browser`, `validate` y `build`. Pruebas Android y escenarios reales con codecs, redes móviles, varios espectadores y disco bajo presión. |

## Objetivos y configuración inicial

Medir primer fotograma en Direct Play y remux con objetivos de 2 s y 5 s.
Medir LCP, CLS e INP con objetivos de 2,5 s, 0,1 y 200 ms cuando el escenario
sea comparable. Registrar perfil de CPU, almacenamiento, formato, navegador,
latencia, velocidad, espectadores y descargas concurrentes. Medir también
picos de CPU y RAM, tiempo con buffering, bytes transferidos y procesos vivos
tras cerrar la reproducción.

En un servidor pequeño, empezar con `MAX_LIVE_REMUXES=1` si se prioriza la
latencia para un espectador, o mantener `2` si suelen reproducir dos usuarios
a la vez. `AUTO_CACHE_ON_WATCH=false` evita codificaciones completas
secundarias a cambio de tener que volver a convertir archivos incompatibles
en la siguiente reproducción. Ajustar `MAX_CACHE_SIZE_GB` al volumen y
conservar espacio para base de datos, logs y descargas mediante
`MIN_FREE_DISK_GB`. Mantener `MEDIA_ACCEL_REDIRECT=true` con el nginx incluido
y su montaje de solo lectura. [Configuración de la instalación](SETUP.md#playback-performance-and-storage-maintenance).

## Migración y rollback del primer tramo

El primer tramo no necesita migración de esquema ni variables nuevas. El
directorio oculto `.leases` se crea en la caché cuando se necesita. Los locks
se liberan al cerrar el descriptor o terminar el proceso. Con varios procesos
core, actualizarlos como un conjunto para que todos respeten los mismos locks.
El mecanismo usa hasta 256 archivos de lock en el volumen Linux compartido.
Si dos copias coinciden en el mismo lock, la limpieza de ambas espera a que
queden libres.
Validar el soporte de locking del almacenamiento antes de usar volúmenes de red.

Para revertir, volver a la imagen anterior del core y conservar los volúmenes,
el directorio de caché y la configuración existente. Antes de migraciones
posteriores, comprobar backup y restauración en una réplica y documentar si la
imagen anterior puede abrir el esquema nuevo.

## Migración y rollback del segundo tramo

Core y downloader deben compartir `DATA_ROOT` y usar el mismo punto de montaje
para descargas. Actualizar ambos juntos. Al arrancar, el core construye el
índice de biblioteca en segundo plano. Se crea `library-*.db` al lado de las
demás bases de datos. El actualizador incluye este archivo mediante una
instantánea SQLite consistente. Las correcciones se conservan por raíz y ruta
relativa. Si se renombra un archivo externamente, la reconciliación registra
la nueva ruta. Volver a aplicar la corrección cuando corresponda.

Para revertir, volver a las imágenes anteriores de core y downloader y
conservar el volumen de datos. Las versiones anteriores usan su escaneo
habitual y las correcciones del índice quedan disponibles para otra
actualización. Conservar `library-*.db` en los backups. El directorio
`runtime/space-reservations` contiene el registro auxiliar de trabajos en vivo.

## Migración y rollback de la cola de descargas

Actualizar core y downloader juntos sobre los volúmenes existentes. La cola
se crea automáticamente en `DATA_ROOT/runtime/jobs-*/queue.sqlite3` y
conserva los trabajos al recrear sus contenedores. El directorio pertenece
al volumen activo y queda fuera de los backups del actualizador. Los trabajos
de una versión anterior que solo existían en memoria deben terminar antes
del cambio para conservar su estado.

Para revertir, conservar los volúmenes y desplegar las imágenes anteriores
de core y downloader. La versión anterior ignora la cola nueva. Cada
descarga gestionada conserva un marcador `.streamvault-managed.part`
hasta que sus archivos pasan la verificación. Ese marcador también impide que
un arranque de la versión anterior recupere un parcial como archivo completo.

## Migración y rollback del cierre

Las migraciones del core añaden columnas e índices en una transacción. Los
índices `ix_federated_lookup` y `history_continue` pueden quedarse al volver
a las imágenes anteriores. Para revertir, usar la revisión anterior de
Compose e imágenes y conservar el volumen. La copia de configuración y
SQLite del actualizador sirve para recuperación si se dañan esos datos.
Los locks de mantenimiento se liberan al cerrar el proceso.

La aceleración VAAPI, QSV o NVENC es optativa. El software sigue como valor
predeterminado. El uso real de GPU, mapeo HDR a SDR, HLS adaptativo cuando el
hardware o la red lo justifiquen y la latencia sobre conexiones WAN siguen
como verificaciones o mejoras de una futura entrega. La [guía de instalación](SETUP.md#deploying-and-monitoring-the-updated-services)
documenta las variables y el monitor operativo.
