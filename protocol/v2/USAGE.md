# Guía Rápida de Uso Diario (Protocolo V2)

1. **Consultar qué tenés pendiente (1 lectura)**:
   - Obtener el SHA actual de `main` vía GitHub API.
   - Leer `https://raw.githubusercontent.com/renlinx-gt/cu-erp-ai-test/<SHA>/protocol/v2/inboxes/<tu_agente>.json`.
2. **Publicar mensaje, batch, ACK o actualizar una tarea/checkpoint**:
   - Crear una rama nueva desde `main`.
   - Agregar archivo inmutable en `protocol/v2/messages/<thread_id>/<msg_id>.json` (o actualizar `protocol/v2/tasks/<task_id>.json` con tu `checkpoint`).
   - Regenerar feeds y bandejas dentro del PR:
     ```bash
     python3 tools/validate_v2.py --root . --build-index
     python3 tools/validate_v2.py --root . --base-ref origin/main --check-index
     ```
   - Abrir Pull Request hacia `main` y esperar el check verde antes de aprobar el merge humano.
