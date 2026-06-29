# SmartRecorder — Especificación técnica

> Detalle de arquitectura y de cada componente. Para qué hace cada feature ver [FUNCTIONAL.md](FUNCTIONAL.md); para visión general ver [OVERVIEW.md](OVERVIEW.md).

## 1. Mapa de componentes

```
main.py                              ← entry point, tema oscuro, estilo Fusion
└── SmartRecorderApp (src/app.py)    ← ventana principal PySide6, conecta todo
    ├── Recorder       (src/recorder.py)              ← captura de audio (mic / loopback / mezcla)
    ├── Transcriber    (src/transcriber.py)           ← 3 motores de transcripción, async
    ├── SummarizationAgent (src/agents/agent_summarization.py) ← resumen vía LLM
    ├── ChatAgent      (src/agents/agent_orchestrator.py)      ← chat con tool-calling
    ├── BaseAgent      (src/agents/base.py)           ← loop genérico de tool-calling + selección de proveedor LLM
    └── db             (src/db.py)                    ← SQLite, toda la persistencia
```

## 2. Modelo de hilos

Todo trabajo pesado (grabar, transcribir, resumir, chat) corre en hilos `daemon` independientes. Los resultados se pasan al hilo principal de Qt **solo** vía `Signal.emit()` — nunca se debe tocar un widget Qt directamente desde un hilo worker.

| Signal | Emitido por | Conectado a |
|---|---|---|
| `_sig_recording_done` | `Recorder` (callback) | `_on_done` |
| `_sig_transcribed` | `Transcriber` (callback) | `_on_transcribed` |
| `_sig_summarized` | `SummarizationAgent` (callback) | `_on_summarized` |
| `_sig_agent_response` | `ChatAgent` (callback) | `_on_agent_response` |

## 3. `src/recorder.py` — Captura de audio

- Usa `pyaudiowpatch` (WASAPI loopback) para capturar audio del sistema en Windows.
- `run_mic()` / `run_loopback()` / `run_both()` — tres modos, seleccionados según los toggles de Mic/App Audio.
- `run_both()` mezcla dos streams independientes (mic + loopback) muestreados a tasas distintas; si el stream de loopback no captura nada (p.ej. no había audio del sistema), se hace fallback a guardar solo el micrófono en vez de descartar la grabación.
- Constantes `FOLDER` (recordings activos) y `ARCHIVE_FOLDER` (archivados), ambas bajo `DATA_DIR` (ver `db.py`).

## 4. `src/transcriber.py` — Transcripción (3 motores)

```python
ENGINE_WHISPERX = "whisperx"  # gratis, local, rápido — DEFAULT
ENGINE_FASTER   = "faster"    # gratis, local, simple (sin dependencia de ffmpeg)
ENGINE_API      = "api"       # pago, OpenAI Whisper API
```

| Motor | Librería | Notas |
|---|---|---|
| `whisperx` | `whisperx` (CTranslate2 + batching) | Modelo `"base"`, `compute_type="int8"`, `batch_size=min(16, cpu_count)`. Requiere `ffmpeg.exe` (se descarga solo, ver `_ensure_ffmpeg`). ~5x más rápido que tiempo real en CPU. |
| `faster` | `faster_whisper.WhisperModel` | Modelo `"small"`, `compute_type="int8"`. Sin dependencia de ffmpeg (decodifica WAV directamente). |
| `api` | `openai.OpenAI().audio.transcriptions` | Límite de 25 MB/archivo — los `.wav` se troz mediante `_split_wav` (corta por frames PCM, sin perder integridad del WAV); otros formatos (vídeo) no se pueden trocear y deben pesar <25 MB. |

Los 3 motores **no fuerzan idioma** — auto-detectan (parámetro `language` omitido), lo que da soporte nativo a ~95 idiomas incluyendo español e inglés.

`_ensure_ffmpeg()`: comprueba si existe `tools/ffmpeg/ffmpeg.exe` (gitignored); si no, descarga el build "essentials" de gyan.dev (~100 MB), extrae solo el ejecutable, y añade esa carpeta a `PATH` en tiempo de ejecución. Solo se ejecuta la primera vez que se usa WhisperX.

`transcribe_async(filepath, on_done, engine)` corre en un hilo daemon; `on_done(filepath, text_or_exception)` — si falla, se pasa la excepción real (no un mensaje genérico) para que la UI muestre la causa.

## 5. `src/db.py` — Persistencia

- SQLite en `DATA_DIR/smart_recorder.db`. `DATA_DIR` por defecto es relativo al proyecto (`<repo>/data/`), no hardcodeado a un usuario concreto.
- Tabla `recordings`: `filepath` (clave actual — **ver sección 8, pendiente de migrar a `id`**), `source`, `start_time`, `end_time`, `duration`, `sample_rate`, `channels`, `file_size`, `transcript`, `summary`, `project`, `meeting`.
- Migraciones de esquema vía `_SCHEMA_VERSION` + bloques `ALTER TABLE ADD COLUMN` envueltos en `try/except OperationalError` (nunca destructivas).
- `load_metadata(folder)`: reconcilia filas de la BD con archivos físicos — si un archivo se movió, lo reconecta por nombre de archivo; si no se encuentra, marca `file_missing=True` sin borrar metadata. También auto-importa WAVs sueltos no registrados.
- `clear_transcript_and_summary(filepath)`: usado por el flujo de borrado en 2 pasos (borra transcripción/resumen sin borrar la fila).

## 6. `src/app.py` — UI (PySide6)

Puntos clave de implementación:
- `RecordingRow(QFrame)`: fila de la lista de grabaciones, con hover manual (`WA_Hover` + `event()` override) porque `:hover` de QSS es poco fiable en listas dinámicas. `WA_AlwaysShowToolTips` activado para que los tooltips funcionen también en botones desactivados.
- `_make_row(rec)`: reconstruye la fila completa desde cero en cada `_rebuild_list()` — no hay actualización incremental.
- Reproductor: `QMediaPlayer` + `QAudioOutput` (requiere `PySide6-Addons` / `QtMultimedia`). Barra de progreso (`QSlider`) + botones ±10s.
- Importación de audio/vídeo: `QFileDialog.getOpenFileName` con filtro `_IMPORT_EXTENSIONS` (vídeo + audio); copia el archivo a `FOLDER/<proyecto>/` y lo registra con `save_metadata(..., source="video"|"audio", ...)`.
- Archivado: mueve el archivo a `ARCHIVE_FOLDER/<proyecto>/` (copy2 + remove explícito, **nunca `shutil.move`** — en carpetas sincronizadas con OneDrive, `move` puede copiar sin borrar el origen y generar duplicados fantasma).
- Borrado en 2 pasos: `QMessageBox` con 3 botones (Archivar / Eliminar / Cancelar) → si Eliminar, segunda pregunta opcional sobre transcripción/resumen.
- Chat: bubbles con `QLabel` + `setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)` para permitir copiar texto sin los problemas de tamaño de `QPlainTextEdit`.

## 7. `src/agents/` — Capa de IA

- `base.py::make_llm_client()`: detecta qué clave de entorno existe (`DEEPSEEK_API_KEY` primero, si no `OPENAI_API_KEY`) y devuelve un cliente OpenAI-SDK-compatible apuntando al proveedor correspondiente. Necesario porque la red corporativa bloquea DeepSeek para parte del equipo.
- `base.py::BaseAgent._run_loop`: loop genérico de tool-calling (pide completion → si hay `tool_calls`, los ejecuta y vuelve a pedir → si no, devuelve texto final).
- `agent_summarization.py::SummarizationAgent`: prompt fijo en español, una sola tool `guardar_resumen`.
- `agent_orchestrator.py::ChatAgent`: prompt de asistente general, una sola tool `buscar_grabaciones` (búsqueda por palabra clave sobre `db.search_transcripts`).

## 8. Entorno de desarrollo

- **venv real**: `C:\venvs\smartrecorder`, **Python 3.12** (no 3.13 ni 3.14 — `ctranslate2`/`whisperx` no tienen rueda para 3.14; el Python 3.13 de Microsoft Store de la máquina de desarrollo está roto).
- Certificados corporativos: `pip install pip-system-certs` (necesario por inspección SSL de Zscaler).
- `launch_silent.vbs`: lanzador portátil, autolocaliza su carpeta, prueba varias rutas de `pythonw.exe` en orden (`.venv` local del proyecto → `C:\venvs\smartrecorder` → PATH del sistema).
- Anclaje a la barra de tareas: **no se puede scriptear** en builds recientes de Windows (el verbo de shell "Anclar a la barra de tareas" ya no se expone a apps sin firmar/Store). Hay que hacerlo a mano (clic derecho → Anclar).

## 9. Pendiente — integración con la rama del compañero

El compañero subió a `origin/main` (sin fusionar todavía en esta rama) cambios que tocan los mismos archivos centrales:

| Cambio | Archivo afectado | Naturaleza |
|---|---|---|
| Clave de `recordings` pasa de `filepath` a `id` (INTEGER) | `db.py`, todo `app.py` | Refactor estructural — mejora real, pero toca cada función de acceso a BD |
| `transcript_raw` + corrección vía `gpt-4o-mini` | `transcriber.py`, `db.py` | Aditivo — guarda ambas versiones |
| `language` detectado y usado para elegir idioma del resumen | `transcriber.py`, `db.py`, `agent_summarization.py` | Aditivo |
| Resumen estructurado (3 secciones fijas) | `agent_summarization.py` | Cambio de prompt + parseo en `app.py` para renderizar HTML |
| WhisperX con diarización (`pyannote`, necesita `HF_TOKEN`) | `transcriber_x.py` (nuevo), `app.py`, `db.py` | Aditivo, opcional (botones ⟳X / TX aparte) |

**Plan de fusión**: adoptar el esquema por `id` como base (resuelve de raíz el tipo de bug de duplicados que parcheamos a mano), reconstruir las features propias (3 motores, importación audio/vídeo, archivado, borrado en 2 pasos, reproductor, copiar al portapapeles) sobre ese esquema, e incorporar raw+corrección+idioma+resumen estructurado+diarización tal cual del compañero. Es un trabajo de fusión no trivial porque ambas ramas reescriben los mismos métodos de `app.py`/`db.py` — se hace como tarea propia, no de forma incidental.
