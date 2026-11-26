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
| 5 · Identificación | Analizar archivos por componentes y puntuar candidatos por título original y localizado, año, episodio, duración e identificadores. Evaluar GuessIt y RapidFuzz. Conservar correcciones manuales y presentar coincidencias ambiguas al administrador. | Fixtures con años en títulos, temporadas completas, capítulos múltiples y ediciones distintas. Verificar que se preservan las rutas originales. |
| 6 · Índice | Índice SQLite incremental por ruta y huella de tamaño/mtime. Reconciliar cambios externos y procesar eventos de importación, borrado y finalización. Evaluar FTS5. | Buscar y paginar bibliotecas de miles de archivos. Mantener disponibilidad durante reconciliación y reutilizar FFprobe para archivos sin cambios. |
| 7 · TMDB | El límite de entradas de la LRU ya se aplica. Añadir límite de bytes, TTL por recurso, persistencia y actualización asíncrona. Reutilizar conexiones y servir datos previos ante fallos. | Caché saturada, peticiones simultáneas, latencia, 429 y 5xx. Los archivos locales siguen disponibles con TMDB caído. |
| 8 · Disco | Clasificar originales, parciales recuperables, caché, pistas, segmentos, backups y logs. Introducir reservas compartidas y perfiles Minimal, Balanced y Performance. Mantener vista previa y aplicar TTL/LRU solo a categorías previstas. | Dos escritores con el disco limitado, recuperación de un proceso terminado y limpieza concurrente. El primer tramo protege copias en uso entre procesos. |
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
