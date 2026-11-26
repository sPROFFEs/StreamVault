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
| 7 · TMDB | El límite de entradas de la LRU ya se aplica. Añadir límite de bytes, TTL por recurso, persistencia y actualización asíncrona. Reutilizar conexiones y servir datos previos ante fallos. | Caché saturada, peticiones simultáneas, latencia, 429 y 5xx. Los archivos locales siguen disponibles con TMDB caído. |
| 8 · Disco | El segundo tramo incorpora reservas compartidas, perfiles Minimal, Balanced y Performance, revisión de parciales recuperables y retención opcional de backups. La caché regenerable conserva TTL y LRU. | Dos escritores con presupuesto compartido, liberación tras caída, revisión de parciales y protección de activos. |
| 9 · Descargas | Persistir cola y transiciones idempotentes. Validar TLS, reanudar cuando sea compatible, comprobar integridad y publicar archivos completos de forma atómica. | Cancelación efectiva y reinicio en cada estado. Una temporada interrumpida conserva los episodios completos y los parciales recuperables. |
| 10 · Web | Mostrar fuentes locales temprano, abortar peticiones obsoletas y conservar la sesión de reproducción al añadir fuentes. Separar módulos, liberar listeners, temporizadores y Blob URLs y medir carga de imágenes, DOM, paginación y virtualización. | Chromium móvil, escritorio y TV, navegación por mando, offline, cambio de episodio y posición preservada ante actualizaciones secundarias. |
| 11 · SQLite | Medir consultas, añadir índices según planes de ejecución, agrupar progreso, acortar transacciones y coordinar tareas periódicas. | Migraciones compatibles, contención controlada, copia consistente y apagado ordenado durante reproducción o descarga. |
| 12 · Operaciones | Versiones reproducibles, límites ajustables, healthchecks, rotación y privilegios mínimos del actualizador. Panel ligero para disco, reservas, copias regenerables, CPU, RAM, reproducción, colas y proveedores. | Instalar y actualizar una instancia de prueba con sus volúmenes. Probar dry-run del cleaner y simular arranque de nginx y del túnel. |
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
