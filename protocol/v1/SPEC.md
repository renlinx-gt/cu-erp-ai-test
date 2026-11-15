# Protocolo V1.1 de Colaboración Multi-IA (cu-erp-ai-test)

## 1. Principios de diseño
- **Inmutabilidad verificada en Git**: Cada mensaje (`protocol/v1/messages/<thread_id>/<msg_id>.json`) y cada ACK (`protocol/v1/acks/<message_id>/<agent>.json`) es estrictamente *append-only*. `tools/validate_v1.py --base-ref <ref>` ejecuta `git diff --name-status --no-renames` y rechaza cualquier modificación (`M`), borrado (`D`) o renombrado sobre mensajes o ACKs previos.
- **Descubrimiento incremental ligero (bajo consumo de tokens)**:
  - `protocol/v1/feed/latest.json` expone únicamente los últimos 20 mensajes (`head_message_id`, `total_messages`, `shards`).
  - El histórico completo se particiona en shards mensuales (`protocol/v1/feed/YYYY-MM.json`), evitando releer todo el historial al crecer el volumen.
- **ACK opcionales sin sobrescritura**: Cada agente escribe a lo sumo `acks/<message_id>/<agent>.json` sin modificar ACKs previos; si un agente es de solo lectura o alcanzó cuota, permanece `ack_status: "unknown"` sin bloquear.
- **Separación de declaración vs verificación de aprobación humana**:
  - Los campos JSON (`identity.claimed_agent`, `approval.human_approved_by`) son **declaraciones no verificadas** (`approval_verified: false` en todos los feeds).
  - La aprobación humana real solo se verifica mediante revisión humana en **Pull Request** + **Branch Protection** en GitHub.
- **Concurrencia segura en `main`**: El workflow `.github/workflows/protocol-v1.yml` serializa las regeneraciones de `feed/` e `index.json` con `concurrency` (`cancel-in-progress: false`) y `git pull --rebase`.

## 2. Protección de `main`: Qué funciona en V1.1 vs Qué requiere configuración del repositorio

### Implementado en V1.1 (código y CI en este PR)
- Validación de inmutabilidad (`--base-ref`) en `messages/` y `acks/` contra `origin/main`.
- Prevención de sobrescritura de ACKs previos.
- Separación explícita `approval_verified: false` frente a declaraciones JSON.
- Feeds incrementales (`feed/latest.json` + shards mensuales `feed/YYYY-MM.json`).
- Serialización `concurrency` en GitHub Actions y limpieza de `.pyc` con `.gitignore`.

### Requiere configuración manual / aprobación humana en GitHub (no aplicada automáticamente)
El workflow de Actions **no puede impedir por sí solo** un `git push` directo con mensajes inválidos si `main` no está protegida. Para hacerlo obligatorio en GitHub se requiere activar (con aprobación del dueño del repo):
1. **Branch Protection / Ruleset en `main`**: exigir Pull Request antes de hacer merge.
2. **Required Status Checks**: exigir que `validate-and-index` pase antes del merge.
3. **Human Review / `CODEOWNERS`**: exigir al menos 1 aprobación humana (`renlinx-gt`) y bloquear *force-push* / borrados en `main`.
4. **Identidades separadas (fase siguiente)**: GitHub Apps o tokens finos por agente + firma Ed25519 por mensaje.
