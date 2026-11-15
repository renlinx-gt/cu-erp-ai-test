# Protocolo V2 de Colaboración Multi-IA para ERPNext/Frappe

Construido sobre V1.1 sin alterar el historial previo (`protocol/v1/`).

## Componentes V2
1. **Bandejas por agente (`protocol/v2/inboxes/<agente>.json`)**: cada IA lee únicamente su archivo de bandeja (anclado al SHA de `main`) para ver mensajes pendientes y tareas asignadas con mínimo consumo de tokens.
2. **Lectura incremental (`protocol/v2/feed/latest.json` + `YYYY-MM.json`)**: últimos 20 mensajes y shards mensuales.
3. **Tareas, prioridades, dependencias y checkpoints (`protocol/v2/tasks/<task_id>.json`)**: prioridades `P0..P3`, estados `backlog..done`, `depends_on` validado y `checkpoint` (`last_step`, `resume_hint`, `paused_reason`) para reanudar tras límites de cuota o bloqueos.
4. **Contexto compartido por proyecto (`protocol/v2/projects/<project>.json`)**: estado y ADRs para `cu_gensite`, `cu_fel`, `cu_deploy` y `helpdesk`, referenciando repos reales sin modificarlos.
5. **Mensajes agrupados (`type: "batch"`)**: permite consolidar múltiples avisos/asignaciones en un solo PR y una sola ejecución de CI.
6. **Seguridad y política Git (`tools/validate_v2.py`)**: inmutabilidad estricta en `messages/` y `acks/`, prohibición de borrados en `tasks/` y `projects/`, `approval_verified: false` en índices y prohibición de autoaprobación por IAs.

## Funcional hoy vs Limitaciones pendientes
- **Funcional hoy**: validación local y en CI (`protocol-v2.yml`), inmutabilidad V1.1+V2, bandejas por agente, tareas con dependencias y checkpoints, proyectos ERP referenciados y soporte `batch`.
- **Limitaciones pendientes**:
  1. **Reglas de `main` en GitHub**: si `main` no tiene Branch Protection / Ruleset exigiendo PR + checks (`validate-and-index` y `validate-v2`), una cuenta con `push` directo podría saltarse la validación.
  2. **Identidad compartida (`renlinx-gt`)**: todas las IAs comparten el mismo principal en GitHub hasta configurar GitHub Apps/tokens finos por agente o firmas Ed25519 individuales.
  3. **Repo público de pruebas**: para trabajo real de ERPNext debe usarse un repositorio privado independiente.
