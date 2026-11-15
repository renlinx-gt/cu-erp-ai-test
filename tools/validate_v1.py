#!/usr/bin/env python3
"""Validador V1.1: inmutabilidad Git, esquemas, separación de aprobación y feeds incrementales."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

AGENTS = {"chatgpt", "claude", "gemini", "codex"}
RECIPIENTS = AGENTS | {"human"}
MSG_TYPES = {"consulta", "propuesta", "revision", "bloqueo", "resumen"}
ACK_STATUSES = {"read", "in_progress", "answered", "deferred_quota"}
SCHEMA_VERSIONS = {"1.0", "1.1"}
LATEST_FEED_LIMIT = 20

MSG_ID_RE = re.compile(r"^MSG-[0-9]{8}-[A-Z]+-[0-9]{3,}$")
THREAD_ID_RE = re.compile(r"^THR-[A-Z0-9-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ISO_DT_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(Z|[+-][0-9]{2}:[0-9]{2})$")


class ValidationError(Exception):
    pass


def _check_identity(obj: object, ctx: str) -> dict:
    if not isinstance(obj, dict):
        raise ValidationError(f"{ctx}: identity debe ser un objeto")
    if set(obj.keys()) != {"claimed_agent", "authenticated_principal"}:
        raise ValidationError(f"{ctx}: identity requiere exactamente claimed_agent y authenticated_principal")
    if obj["claimed_agent"] not in AGENTS:
        raise ValidationError(f"{ctx}: claimed_agent inválido: {obj['claimed_agent']!r}")
    prin = obj["authenticated_principal"]
    if not isinstance(prin, str) or not prin.strip():
        raise ValidationError(f"{ctx}: authenticated_principal debe ser string no vacío")
    return obj


def check_immutability(root: Path, base_ref: str) -> None:
    """Verifica que ningún mensaje ni ACK previo haya sido modificado, renombrado o eliminado."""
    cmd = [
        "git", "-C", str(root), "diff", "--name-status", "--no-renames",
        f"{base_ref}..HEAD", "--", "protocol/v1/messages", "protocol/v1/acks",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise ValidationError(f"Error ejecutando git diff contra {base_ref}: {res.stderr.strip()}")

    violations: list[str] = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        status = parts[0].strip()
        path = parts[1].strip() if len(parts) > 1 else ""
        if status != "A":
            violations.append(f"{status} {path}")
    if violations:
        raise ValidationError(
            "Violación de inmutabilidad (solo se permiten archivos nuevos 'A' en messages/ y acks/): "
            + ", ".join(violations)
        )


def validate_repo(root: Path, base_ref: str | None = None) -> tuple[list[tuple[Path, dict]], list[tuple[Path, dict]]]:
    if base_ref:
        check_immutability(root, base_ref)

    v1_dir = root / "protocol" / "v1"
    msg_dir = v1_dir / "messages"
    ack_dir = v1_dir / "acks"

    messages: list[tuple[Path, dict]] = []
    seen_ids: dict[str, Path] = {}

    if msg_dir.exists():
        for path in sorted(msg_dir.rglob("*.json")):
            rel = path.relative_to(root)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValidationError(f"{rel}: JSON inválido: {exc}") from exc

            expected_keys = {
                "schema_version", "id", "thread_id", "type", "created_at",
                "identity", "to", "ref_sha", "reply_to", "summary", "body", "approval",
            }
            if not isinstance(data, dict) or set(data.keys()) != expected_keys:
                raise ValidationError(f"{rel}: claves inválidas o faltantes")
            if data["schema_version"] not in SCHEMA_VERSIONS:
                raise ValidationError(f"{rel}: schema_version inválido: {data['schema_version']!r}")
            msg_id = data["id"]
            if not isinstance(msg_id, str) or not MSG_ID_RE.match(msg_id):
                raise ValidationError(f"{rel}: id inválido: {msg_id!r}")
            if path.name != f"{msg_id}.json":
                raise ValidationError(f"{rel}: el nombre de archivo debe ser {msg_id}.json")
            if msg_id in seen_ids:
                raise ValidationError(f"{rel}: id duplicado {msg_id} (ya existe en {seen_ids[msg_id]})")
            seen_ids[msg_id] = rel

            thread_id = data["thread_id"]
            if not isinstance(thread_id, str) or not THREAD_ID_RE.match(thread_id):
                raise ValidationError(f"{rel}: thread_id inválido: {thread_id!r}")
            if path.parent.name != thread_id:
                raise ValidationError(f"{rel}: debe ubicarse dentro del directorio {thread_id}/")

            if data["type"] not in MSG_TYPES:
                raise ValidationError(f"{rel}: type inválido: {data['type']!r}")
            if not isinstance(data["created_at"], str) or not ISO_DT_RE.match(data["created_at"]):
                raise ValidationError(f"{rel}: created_at debe ser ISO-8601 UTC/offset")

            _check_identity(data["identity"], str(rel))

            to = data["to"]
            if not isinstance(to, list) or not to or any(x not in RECIPIENTS for x in to):
                raise ValidationError(f"{rel}: 'to' debe ser lista no vacía de destinatarios válidos")

            ref_sha = data["ref_sha"]
            if ref_sha is not None and (not isinstance(ref_sha, str) or not SHA_RE.match(ref_sha)):
                raise ValidationError(f"{rel}: ref_sha inválido: {ref_sha!r}")

            reply_to = data["reply_to"]
            if reply_to is not None:
                if not isinstance(reply_to, dict) or set(reply_to.keys()) != {"message_id", "ref_sha"}:
                    raise ValidationError(f"{rel}: reply_to debe tener message_id y ref_sha")
                if not isinstance(reply_to["message_id"], str) or not MSG_ID_RE.match(reply_to["message_id"]):
                    raise ValidationError(f"{rel}: reply_to.message_id inválido")
                if not isinstance(reply_to["ref_sha"], str) or not SHA_RE.match(reply_to["ref_sha"]):
                    raise ValidationError(f"{rel}: reply_to.ref_sha inválido")

            summary = data["summary"]
            if not isinstance(summary, str) or not (1 <= len(summary) <= 240):
                raise ValidationError(f"{rel}: summary debe tener entre 1 y 240 caracteres")
            body = data["body"]
            if not isinstance(body, str) or not body.strip():
                raise ValidationError(f"{rel}: body no puede estar vacío")

            approval = data["approval"]
            if not isinstance(approval, dict) or set(approval.keys()) != {"required", "human_approved_by"}:
                raise ValidationError(f"{rel}: approval inválido")
            if not isinstance(approval["required"], bool):
                raise ValidationError(f"{rel}: approval.required debe ser booleano")
            hab = approval["human_approved_by"]
            if hab is not None:
                if not isinstance(hab, str) or not hab.strip():
                    raise ValidationError(f"{rel}: approval.human_approved_by debe ser string no vacío o null")
                if hab.strip().lower() in AGENTS:
                    raise ValidationError(f"{rel}: una IA ({hab}) no puede figurar como human_approved_by")

            messages.append((path, data))

    for path, data in messages:
        if data["reply_to"] is not None:
            parent_id = data["reply_to"]["message_id"]
            if parent_id not in seen_ids:
                raise ValidationError(f"{path.relative_to(root)}: reply_to apunta a {parent_id} inexistente")

    acks: list[tuple[Path, dict]] = []
    if ack_dir.exists():
        for path in sorted(ack_dir.rglob("*.json")):
            rel = path.relative_to(root)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValidationError(f"{rel}: JSON inválido: {exc}") from exc

            req_keys = {"schema_version", "message_id", "observed_sha", "status", "created_at", "identity"}
            allowed_keys = req_keys | {"note"}
            if not isinstance(data, dict) or not req_keys.issubset(data.keys()) or not set(data.keys()).issubset(allowed_keys):
                raise ValidationError(f"{rel}: claves de ACK inválidas")
            if data["schema_version"] not in SCHEMA_VERSIONS:
                raise ValidationError(f"{rel}: schema_version inválido")
            msg_id = data["message_id"]
            if msg_id not in seen_ids:
                raise ValidationError(f"{rel}: ACK referencia mensaje inexistente {msg_id}")
            if path.parent.name != msg_id:
                raise ValidationError(f"{rel}: ACK debe ubicarse bajo {msg_id}/")
            ident = _check_identity(data["identity"], str(rel))
            if path.name != f"{ident['claimed_agent']}.json":
                raise ValidationError(f"{rel}: el nombre del ACK debe ser {ident['claimed_agent']}.json")
            if not isinstance(data["observed_sha"], str) or not SHA_RE.match(data["observed_sha"]):
                raise ValidationError(f"{rel}: observed_sha inválido")
            if data["status"] not in ACK_STATUSES:
                raise ValidationError(f"{rel}: status inválido: {data['status']!r}")
            if not isinstance(data["created_at"], str) or not ISO_DT_RE.match(data["created_at"]):
                raise ValidationError(f"{rel}: created_at inválido")
            if "note" in data and (not isinstance(data["note"], str) or len(data["note"]) > 240):
                raise ValidationError(f"{rel}: note debe ser string <= 240 chars")
            acks.append((path, data))

    return messages, acks


def _entry_for_message(root: Path, path: Path, msg: dict, ack_map: dict[str, dict[str, dict]]) -> dict:
    mid = msg["id"]
    msg_acks = ack_map.get(mid, {})
    return {
        "id": mid,
        "thread_id": msg["thread_id"],
        "type": msg["type"],
        "from": msg["identity"]["claimed_agent"],
        "authenticated_principal": msg["identity"]["authenticated_principal"],
        "to": msg["to"],
        "created_at": msg["created_at"],
        "reply_to": msg["reply_to"]["message_id"] if msg["reply_to"] else None,
        "approval_required": msg["approval"]["required"],
        "approval_verified": False,
        "approval_verification_note": "La aprobación humana real se verifica únicamente por revisión en PR / Branch Protection, nunca por JSON.",
        "declared_human_approved_by": msg["approval"]["human_approved_by"],
        "summary": msg["summary"],
        "path": path.relative_to(root).as_posix(),
        "ack_status": "recorded" if msg_acks else "unknown",
        "acks": msg_acks,
    }


def build_feeds(
    root: Path,
    messages: list[tuple[Path, dict]],
    acks: list[tuple[Path, dict]],
) -> dict[str, str]:
    ack_map: dict[str, dict[str, dict]] = {}
    for _, ack in acks:
        mid = ack["message_id"]
        agent = ack["identity"]["claimed_agent"]
        ack_map.setdefault(mid, {})[agent] = {
            "status": ack["status"],
            "observed_sha": ack["observed_sha"],
            "created_at": ack["created_at"],
            "authenticated_principal": ack["identity"]["authenticated_principal"],
        }

    ordered = sorted(messages, key=lambda item: (item[1]["created_at"], item[1]["id"]))
    entries = [_entry_for_message(root, p, m, ack_map) for p, m in ordered]

    shards: dict[str, list[dict]] = {}
    for e in entries:
        month = e["created_at"][:7]  # YYYY-MM
        shards.setdefault(month, []).append(e)

    shard_paths = [f"protocol/v1/feed/{m}.json" for m in sorted(shards.keys())]
    latest_entries = entries[-LATEST_FEED_LIMIT:]
    head = entries[-1] if entries else None

    outputs: dict[str, str] = {}
    for month, shard_entries in sorted(shards.items()):
        outputs[f"protocol/v1/feed/{month}.json"] = json.dumps({
            "schema_version": "1.1",
            "shard": month,
            "message_count": len(shard_entries),
            "messages": shard_entries,
        }, indent=2, ensure_ascii=False) + "\n"

    outputs["protocol/v1/feed/latest.json"] = json.dumps({
        "schema_version": "1.1",
        "generated_by": "tools/validate_v1.py",
        "limit": LATEST_FEED_LIMIT,
        "total_messages": len(entries),
        "head_message_id": head["id"] if head else None,
        "head_created_at": head["created_at"] if head else None,
        "shards": shard_paths,
        "messages": latest_entries,
    }, indent=2, ensure_ascii=False) + "\n"

    outputs["protocol/v1/index.json"] = json.dumps({
        "schema_version": "1.1",
        "generated_by": "tools/validate_v1.py",
        "latest_feed": "protocol/v1/feed/latest.json",
        "shards": shard_paths,
        "message_count": len(entries),
        "messages": latest_entries,
    }, indent=2, ensure_ascii=False) + "\n"

    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valida Protocolo V1.1 y genera/verifica feeds incrementales")
    parser.add_argument("--root", default=".", help="Raíz del repositorio")
    parser.add_argument("--base-ref", default="", help="Ref/SHA base para verificar inmutabilidad de messages/ y acks/")
    parser.add_argument("--build-index", action="store_true", help="Regenerar index.json y feeds")
    parser.add_argument("--check-index", action="store_true", help="Verificar que index.json y feeds coincidan")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    base_ref = args.base_ref.strip() or None
    try:
        messages, acks = validate_repo(root, base_ref=base_ref)
    except ValidationError as err:
        print(f"ERROR DE VALIDACIÓN: {err}", file=sys.stderr)
        return 1

    outputs = build_feeds(root, messages, acks)

    if args.build_index:
        for rel_path, content in outputs.items():
            out_path = root / rel_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content, encoding="utf-8")
        print(f"OK: {len(messages)} mensajes y {len(acks)} ACKs validados; feeds e índice actualizados")
        return 0

    if args.check_index:
        for rel_path, expected in outputs.items():
            out_path = root / rel_path
            if not out_path.exists():
                print(f"ERROR: falta {rel_path}", file=sys.stderr)
                return 1
            if out_path.read_text(encoding="utf-8") != expected:
                print(f"ERROR: {rel_path} desactualizado; ejecute --build-index", file=sys.stderr)
                return 1

    print(f"OK: {len(messages)} mensajes y {len(acks)} ACKs válidos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
