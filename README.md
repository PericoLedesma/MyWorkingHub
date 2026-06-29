# SmartRecorder

App de escritorio Windows que grava reuniones (micrófono / audio de sistema / ambos), transcribe el audio (gratis en local o de pago vía API), genera un resumen con IA, y permite consultar el histórico por chat.

## Documentación

- [docs/OVERVIEW.md](docs/OVERVIEW.md) — resumen ejecutivo y backlog
- [docs/FUNCTIONAL.md](docs/FUNCTIONAL.md) — épicas e historias de usuario
- [docs/TECHNICAL.md](docs/TECHNICAL.md) — arquitectura y detalle técnico por componente

## Requisitos

- Windows (la captura de audio de sistema usa WASAPI loopback, solo Windows)
- Python 3.12 (WhisperX no soporta 3.13+/3.14 todavía)
- `OPENAI_API_KEY` y/o `DEEPSEEK_API_KEY` como variables de entorno (al menos una; el chat/resumen usa la que esté disponible)

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

```powershell
$env:OPENAI_API_KEY   = "sk-..."
$env:DEEPSEEK_API_KEY = "sk-..."
```

## Uso

```powershell
python main.py
```

O usa el acceso directo `Smart Recorder.lnk` / `launch_silent.vbs` (sin consola).
