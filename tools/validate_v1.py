#!/usr/bin/env python3
"""Validador y generador de índice para el Protocolo Multi-IA V1 (sin dependencias externas)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

AGENTS = {"chatgpt", "claude", "gemini", "codex"}
RECIPIENTS = AGENTS | {"human"}
MSG_TYPES = {"consulta", "propuesta", "revision", "bloqueo", "resumen"}
ACK_STATUSES = {"read", "in_progress", "answered", "deferred_quota"}

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


def validate_repo(root: Path) -> tuple[list[tuple[Path, dict]], list[tuple[Path, dict]]]:
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
            if data["schema_version"] != "1.0":
                raise ValidationError(f"{rel}: schema_version debe ser '1.0'")
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

    # Validate reply_to references exist
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
            if data["schema_version"] != "1.0":
                raise ValidationError(f"{rel}: schema_version debe ser '1.0'")
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


def build_index_dict(
    root: Path,
    messages: list[tuple[Path, dict]],
    acks: list[tuple[Path, dict]],
) -> dict:
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

    entries = []
    for path, msg in sorted(messages, key=lambda item: (item[1]["created_at"], item[1]["id"])):
        mid = msg["id"]
        msg_acks = ack_map.get(mid, {})
        entries.append({
            "id": mid,
            "thread_id": msg["thread_id"],
            "type": msg["type"],
            "from": msg["identity"]["claimed_agent"],
            "authenticated_principal": msg["identity"]["authenticated_principal"],
            "to": msg["to"],
            "created_at": msg["created_at"],
            "reply_to": msg["reply_to"]["message_id"] if msg["reply_to"] else None,
            "human_approved_by": msg["approval"]["human_approved_by"],
            "summary": msg["summary"],
            "path": path.relative_to(root).as_posix(),
            "ack_status": "recorded" if msg_acks else "unknown",
            "acks": msg_acks,
        })

    return {
        "schema_version": "1.0",
        "generated_by": "tools/validate_v1.py",
        "message_count": len(entries),
        "messages": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valida Protocolo V1 y genera/verifica index.json")
    parser.add_argument("--root", default=".", help="Raíz del repositorio")
    parser.add_argument("--build-index", action="store_true", help="Regenerar protocol/v1/index.json")
    parser.add_argument("--check-index", action="store_true", help="Verificar que index.json coincida")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        messages, acks = validate_repo(root)
    except ValidationError as err:
        print(f"ERROR DE VALIDACIÓN: {err}", file=sys.stderr)
        return 1

    index_path = root / "protocol" / "v1" / "index.json"
    expected_json = json.dumps(build_index_dict(root, messages, acks), indent=2, ensure_ascii=False) + "\n"

    if args.build_index:
        index_path.write_text(expected_json, encoding="utf-8")
        print(f"OK: {len(messages)} mensajes y {len(acks)} ACKs validados; índice actualizado en {index_path.relative_to(root)}")
        return 0

    if args.check_index:
        if not index_path.exists():
            print("ERROR: falta protocol/v1/index.json", file=sys.stderr)
            return 1
        actual = index_path.read_text(encoding="utf-8")
        if actual != expected_json:
            print("ERROR: protocol/v1/index.json desactualizado; ejecute --build-index", file=sys.stderr)
            return 1

    print(f"OK: {len(messages)} mensajes y {len(acks)} ACKs válidos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
