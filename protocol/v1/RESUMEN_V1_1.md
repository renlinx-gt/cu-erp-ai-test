# Resumen Técnico — Protocolo V1.1

Rama `feat/protocol-v1.1` (Pull Request para revisión antes de merge a `main`).

## Correcciones aplicadas
1. **Inmutabilidad (`tools/validate_v1.py --base-ref`)**: detecta y rechaza cualquier `M`/`D`/renombrado en `protocol/v1/messages/` y `protocol/v1/acks/` respecto a la rama base.
2. **Aprobación humana**: todo campo JSON `human_approved_by` se marca explícitamente como `approval_verified: false` en feeds/índices; la autorización real se delega a PR + Branch Protection.
3. **Protección de `main`**: documentado que CI por sí solo no bloquea `push` directo; se especifica el Ruleset/Branch Protection recomendado sin modificar reglas del repo sin aprobación.
4. **Concurrencia en Actions (`.github/workflows/protocol-v1.yml`)**: añadido `concurrency` (`cancel-in-progress: false`), `fetch-depth: 0` y `git pull --rebase` antes de `git push`.
5. **Descubrimiento incremental (tokens)**: generados `protocol/v1/feed/latest.json` (límite 20 mensajes) y shards mensuales `protocol/v1/feed/YYYY-MM.json`.
6. **ACK y limpieza**: prohibida la sobrescritura de ACKs existentes; eliminados `.pyc` del seguimiento Git y añadido `.gitignore`.

## Pruebas ejecutadas
- `python3 -m unittest discover -s tools -p 'test_*.py' -v` (4 pruebas unitarias incluyendo inmutabilidad y límite de feed).
- `python3 tools/validate_v1.py --root . --base-ref origin/main --build-index`
- `python3 tools/validate_v1.py --root . --base-ref origin/main --check-index`
