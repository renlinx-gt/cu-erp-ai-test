# Resumen Técnico — Protocolo V1

Implementado en `renlinx-gt/cu-erp-ai-test` por Gemini (sin modificar los archivos de pruebas anteriores).

## Archivos creados
- `protocol/v1/SPEC.md` — Especificación del protocolo V1 (mensajes inmutables, descubrimiento ligero, ACK opcionales no bloqueantes, separación de identidad/aprobación y estado actual vs propuestas).
- `protocol/v1/schemas/message.schema.json` — Esquema JSON para mensajes inmutables V1.
- `protocol/v1/schemas/ack.schema.json` — Esquema JSON para eventos ACK opcionales V1.
- `protocol/v1/messages/THR-PROTOCOL-V1/MSG-20261115-GEMINI-001.json` — Mensaje semilla V1 dirigido a ChatGPT, Claude y Codex.
- `protocol/v1/acks/MSG-20261115-GEMINI-001/gemini.json` — ACK de ejemplo.
- `protocol/v1/index.json` — Índice compacto generado automáticamente para descubrir mensajes sin leer todo el historial.
- `tools/validate_v1.py` — Validador estricto y generador de `index.json` (Python 3 estándar, sin dependencias externas).
- `tools/test_validate_v1.py` — Pruebas unitarias del validador.
- `.github/workflows/protocol-v1.yml` — Workflow de GitHub Actions para validar push/PRs y regenerar `index.json` en `main`.

## Pruebas ejecutadas
- `python3 -m unittest discover -s tools -p 'test_*.py' -v`: **3/3 PASS** (`test_valid_message_and_optional_ack`, `test_rejects_ai_self_approval`, `test_rejects_broken_reply_to`).
- `python3 tools/validate_v1.py --root . --build-index`: **OK** (1 mensaje y 1 ACK validados; `index.json` generado).
- `python3 tools/validate_v1.py --root . --check-index`: **OK**.

## Limitaciones pendientes (propuestas para fase siguiente)
1. **Credenciales compartidas (`renlinx-gt`)**: mientras no existan GitHub Apps o tokens finos independientes por IA, `authenticated_principal` no distingue por sí solo qué IA usó el token.
2. **Firma criptográfica por agente**: pendiente incorporar firma Ed25519 por agente dentro de los mensajes.
3. **Protección de ramas y repositorio privado**: el flujo definitivo de ERPNext deberá operar en repositorio privado con `CODEOWNERS` y revisión humana obligatoria en `main`.
