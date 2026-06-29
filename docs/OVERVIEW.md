# SmartRecorder — Resumen ejecutivo

> Documento de referencia rápida. Para detalle funcional ver [FUNCTIONAL.md](FUNCTIONAL.md); para detalle técnico ver [TECHNICAL.md](TECHNICAL.md).

## Qué es

Aplicación de escritorio para Windows que grava reuniones (micrófono, audio del sistema, o ambos), transcribe el audio automáticamente, genera un resumen con IA, y permite consultar todo el histórico de grabaciones mediante un asistente de chat.

## Qué resuelve

- Evita tomar notas manuales en reuniones — se grava, transcribe y resume solo.
- Permite buscar "qué se dijo sobre X" en cualquier reunión pasada sin tener que volver a escuchar el audio.
- Soporta tanto transcripción gratuita (local, sin coste) como de pago (más rápida/precisa), para adaptarse a quien no tiene presupuesto de API o quien prefiere velocidad.

## Estado actual — funcionalidades en producción

| Área | Estado |
|---|---|
| Grabación (mic / sistema / ambos) | ✅ Funcional |
| Transcripción local gratuita (WhisperX, por defecto) | ✅ Funcional |
| Transcripción local gratuita simple (faster-whisper) | ✅ Funcional |
| Transcripción de pago (OpenAI API) | ✅ Funcional |
| Auto-detección de idioma (español, inglés, ~95 idiomas) | ✅ Funcional |
| Importar audio/vídeo externo para transcribir | ✅ Funcional |
| Resumen automático con IA | ✅ Funcional (formato simple, ver backlog) |
| Archivado de grabaciones | ✅ Funcional |
| Reproductor con control de avance/retroceso | ✅ Funcional |
| Borrado seguro (audio / transcripción por separado) | ✅ Funcional |
| Chat asistente sobre el histórico de grabaciones | ✅ Funcional (básico — solo búsqueda) |
| Etiquetado por Proyecto / Reunión | ✅ Funcional |
| Acceso directo de escritorio/menú Inicio | ✅ Funcional (anclar a barra de tareas: manual) |

## Backlog (pendiente, priorizado)

1. **Integrar mejoras del compañero** (rama paralela en GitHub, no fusionada aún):
   - Identificar grabaciones por `id` en vez de ruta de archivo (corrige bugs de archivos duplicados/movidos)
   - Transcripción en bruto + corrección automática vía IA (arregla solo palabras mal oídas)
   - Resumen estructurado por secciones (Temas clave / Decisiones / Puntos de acción)
   - Transcripción con identificación de quién habla (diarización, requiere configuración extra)
2. **Más capacidades para el asistente de chat**: listar grabaciones, leer transcripciones/resúmenes, etiquetar, borrar y controlar la grabación por voz/texto (hoy solo puede buscar)
3. **Memoria persistente del chat** (hoy se olvida todo al cerrar la app)
4. Mejorar la velocidad de la transcripción gratuita (actualmente ~10-12 min por hora de audio; objetivo ideal 5-10 min)
5. Documento de organización en carpetas/notas (parcialmente cubierto por archivado y proyectos)

## Decisiones de producto relevantes

- **La transcripción gratuita es la opción por defecto** — controla costes; la de pago queda disponible para quien la necesite.
- **La auto-transcripción es opcional** (apagada por defecto) — el usuario decide cuándo merece la pena procesar una grabación.
- **Compatibilidad con dos proveedores de IA** (DeepSeek u OpenAI, el que esté disponible) porque la red corporativa bloquea uno de ellos para parte del equipo.
