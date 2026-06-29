# SmartRecorder — Especificación funcional

> Épicas e historias de usuario. Para arquitectura y detalle técnico ver [TECHNICAL.md](TECHNICAL.md). Para visión general ver [OVERVIEW.md](OVERVIEW.md).

Leyenda de estado: ✅ Hecho · 🔶 Parcial · ⬜ Pendiente

---

## Épica 1 — Grabación de audio

**Objetivo**: capturar el audio de una reunión sin que el usuario tenga que gestionar archivos manualmente.

| # | Historia de usuario | Estado |
|---|---|---|
| 1.1 | Como usuario, quiero grabar solo mi micrófono. | ✅ |
| 1.2 | Como usuario, quiero grabar solo el audio del sistema (lo que se oye por los altavoces/cascos). | ✅ |
| 1.3 | Como usuario, quiero grabar micrófono + sistema a la vez, mezclados en un único archivo. | ✅ |
| 1.4 | Como usuario, quiero que ambas fuentes estén activadas por defecto al abrir la app. | ✅ |
| 1.5 | Como usuario, quiero ver en tiempo real el nivel de audio de cada fuente (medidor VU). | ✅ |
| 1.6 | Como usuario, quiero ver un cronómetro mientras grabo. | ✅ |
| 1.7 | Como usuario, si el audio del sistema no capta nada, quiero que se guarde igualmente con el micrófono en vez de perder la grabación. | ✅ |

---

## Épica 2 — Transcripción

**Objetivo**: convertir el audio grabado en texto, eligiendo entre coste cero o máxima velocidad/precisión.

| # | Historia de usuario | Estado |
|---|---|---|
| 2.1 | Como usuario, quiero transcribir gratis, sin pagar por ninguna API. | ✅ |
| 2.2 | Como usuario, quiero una opción de pago más rápida/precisa si la necesito puntualmente. | ✅ |
| 2.3 | Como usuario, quiero que la opción gratuita sea la que se usa por defecto. | ✅ |
| 2.4 | Como usuario, quiero decidir yo cuándo se transcribe una grabación (no que se haga sola y gaste recursos sin que yo lo sepa). | ✅ (auto-transcripción opcional, apagada por defecto) |
| 2.5 | Como usuario, quiero poder volver a transcribir una grabación si el resultado no me convenció. | ✅ (botón ⟳) |
| 2.6 | Como usuario, quiero que funcione igual de bien en español que en inglés u otros idiomas, sin configurarlo a mano. | ✅ (auto-detección de idioma) |
| 2.7 | Como usuario, quiero importar un archivo de audio o vídeo que ya tengo (no grabado con la app) y transcribirlo igual. | ✅ |
| 2.8 | Como usuario, quiero que la transcripción gratuita sea lo más rápida posible (idealmente 5-10 min para 1h de reunión). | 🔶 (actualmente ~10-12 min/h, optimizable) |
| 2.9 | Como usuario, quiero ver tanto el texto "en bruto" de la transcripción como una versión corregida de errores de oído. | ⬜ (en el código del compañero, pendiente de integrar) |
| 2.10 | Como usuario, quiero saber quién dijo qué cuando hay varias personas hablando. | ⬜ (WhisperX con diarización, requiere configuración extra — pendiente) |

---

## Épica 3 — Resumen automático

**Objetivo**: no tener que leer la transcripción completa para saber de qué trató la reunión.

| # | Historia de usuario | Estado |
|---|---|---|
| 3.1 | Como usuario, quiero un resumen automático generado tras la transcripción. | ✅ |
| 3.2 | Como usuario, quiero que el resumen distinga temas clave, decisiones y puntos de acción en secciones separadas. | ⬜ (pendiente, en código del compañero) |
| 3.3 | Como usuario, quiero copiar el resumen con un clic para pegarlo donde necesite. | ✅ |

---

## Épica 4 — Gestión de grabaciones

**Objetivo**: organizar, encontrar y mantener limpio el histórico de grabaciones.

| # | Historia de usuario | Estado |
|---|---|---|
| 4.1 | Como usuario, quiero etiquetar cada grabación con un Proyecto y una Reunión. | ✅ |
| 4.2 | Como usuario, quiero filtrar la lista de grabaciones por proyecto. | ✅ |
| 4.3 | Como usuario, quiero reproducir el audio dentro de la app, con control para avanzar/retroceder. | ✅ |
| 4.4 | Como usuario, quiero archivar grabaciones que no uso activamente, sin perder su transcripción. | ✅ |
| 4.5 | Como usuario, quiero que lo archivado se guarde organizado por proyecto, igual que lo activo. | ✅ |
| 4.6 | Como usuario, quiero borrar el audio sin que me obligue a borrar también la transcripción (decisión en dos pasos). | ✅ |
| 4.7 | Como usuario, quiero abrir la carpeta donde está guardado un archivo con un clic. | ✅ |
| 4.8 | Como usuario, quiero que el sistema no me genere archivos duplicados al mover o archivar grabaciones. | ✅ |

---

## Épica 5 — Asistente de chat

**Objetivo**: hacer preguntas en lenguaje natural sobre el histórico de reuniones.

| # | Historia de usuario | Estado |
|---|---|---|
| 5.1 | Como usuario, quiero preguntarle al asistente si algo se mencionó en alguna reunión pasada. | ✅ (búsqueda por palabra clave) |
| 5.2 | Como usuario, quiero copiar cualquier mensaje del chat. | ✅ |
| 5.3 | Como usuario, quiero que el asistente liste mis grabaciones recientes. | ⬜ |
| 5.4 | Como usuario, quiero que el asistente pueda etiquetar o borrar una grabación si se lo pido por chat. | ⬜ |
| 5.5 | Como usuario, quiero que el asistente pueda iniciar/detener una grabación si se lo pido por chat. | ⬜ |
| 5.6 | Como usuario, quiero que el historial de la conversación se conserve al cerrar y abrir la app. | ⬜ |

---

## Épica 6 — Acceso y configuración

**Objetivo**: que abrir y configurar la app sea sencillo para cualquier miembro del equipo, en cualquier ordenador.

| # | Historia de usuario | Estado |
|---|---|---|
| 6.1 | Como usuario, quiero un acceso directo para abrir la app sin usar la terminal. | ✅ |
| 6.2 | Como usuario, quiero anclar ese acceso a la barra de tareas de Windows. | 🔶 (acceso directo creado; el anclaje final es un clic derecho manual — Windows no permite hacerlo por script) |
| 6.3 | Como usuario, quiero poder usar la app con la clave de API que tenga disponible (OpenAI o DeepSeek), sin que falle si solo tengo una. | ✅ |
| 6.4 | Como usuario, quiero que la ruta donde se guardan mis datos no dependa del usuario de Windows de otra persona. | ✅ |
