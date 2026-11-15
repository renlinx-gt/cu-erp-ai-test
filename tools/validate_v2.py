#!/usr/bin/env python3
"""Validador y generador de feeds/bandejas para el Protocolo Multi-IA V2 (sin dependencias externas)."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from validate_v1 import (
    ACK_STATUSES,
    AGENTS,
    ISO_DT_RE,
    LATEST_FEED_LIMIT,
    MSG_ID_RE,
    RECIPIENTS,
    SHA_RE,
    THREAD_ID_RE,
    ValidationError,
    _check_identity,
    verify_outputs,
    write_outputs,
)

PROJECTS = {"cu_gensite", "cu_fel", "cu_deploy", "helpdesk", "cu_erp_manager", "multi_ai_protocol"}
V2_MSG_TYPES = {"consulta", "propuesta", "revision", "bloqueo", "resumen", "batch"}
TASK_PRIORITIES = {"P0", "P1", "P2", "P3"}
TASK_STATUSES = {"backlog", "ready", "in_progress", "blocked", "review", "done"}
PAUSED_REASONS = {"none", "quota_limit", "blocked", "handoff"}
ADR_STATUSES = {"proposed", "accepted", "superseded"}

TASK_ID_RE = re.compile(r"^TSK-[A-Z0-9-]+-[0-9]{3,}$")
ADR_ID_RE = re.compile(r"^ADR-[0-9]{8}-[0-9]{3,}$")


def check_v2_git_policy(root: Path, base_ref: str) -> None:
    """Exige append-only ('A') en messages/ y acks/ V2, y prohíbe borrados ('D') en tasks/ y projects/."""
    immutable_cmd = [
        "git", "-C", str(root), "diff", "--name-status", "--no-renames",
        f"{base_ref}..HEAD", "--", "protocol/v2/messages", "protocol/v2/acks",
    ]
    res = subprocess.run(immutable_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise ValidationError(f"Error en git diff V2 contra {base_ref}: {res.stderr.strip()}")
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        status, path = (line.split("\t", 1) + [""])[:2]
        if status.strip() != "A":
            raise ValidationError(f"Violación de inmutabilidad V2 ({status.strip()} {path.strip()})")

    nodelete_cmd = [
        "git", "-C", str(root), "diff", "--name-status", "--no-renames",
        f"{base_ref}..HEAD", "--", "protocol/v2/tasks", "protocol/v2/projects",
    ]
    res2 = subprocess.run(nodelete_cmd, capture_output=True, text=True)
    if res2.returncode != 0:
        raise ValidationError(f"Error en git diff tasks/projects contra {base_ref}: {res2.stderr.strip()}")
    for line in res2.stdout.splitlines():
        if not line.strip():
            continue
        status, path = (line.split("\t", 1) + [""])[:2]
        if status.strip() == "D":
            raise ValidationError(f"Prohibido eliminar tareas o proyectos en V2 ({path.strip()})")


def _check_approval(approval: object, ctx: str) -> None:
    if not isinstance(approval, dict) or set(approval.keys()) != {"required", "human_approved_by"}:
        raise ValidationError(f"{ctx}: approval inválido")
    if not isinstance(approval["required"], bool):
        raise ValidationError(f"{ctx}: approval.required debe ser booleano")
    hab = approval["human_approved_by"]
    if hab is not None:
        if not isinstance(hab, str) or not hab.strip():
            raise ValidationError(f"{ctx}: human_approved_by inválido")
        if hab.strip().lower() in AGENTS:
            raise ValidationError(f"{ctx}: una IA ({hab}) no puede figurar como human_approved_by")


def _check_erp_refs(refs: object, ctx: str) -> None:
    if not isinstance(refs, list):
        raise ValidationError(f"{ctx}: erp_refs debe ser una lista")
    for idx, r in enumerate(refs):
        if not isinstance(r, dict) or set(r.keys()) != {"repo", "ref", "path"}:
            raise ValidationError(f"{ctx}: erp_refs[{idx}] debe tener repo, ref y path")
        for k in ("repo", "ref", "path"):
            if not isinstance(r[k], str) or not r[k].strip():
                raise ValidationError(f"{ctx}: erp_refs[{idx}].{k} no puede estar vacío")


def validate_v2(
    root: Path,
    base_ref: str | None = None,
) -> tuple[list[tuple[Path, dict]], list[tuple[Path, dict]], list[tuple[Path, dict]], list[tuple[Path, dict]]]:
    if base_ref:
        check_v2_git_policy(root, base_ref)

    v2 = root / "protocol" / "v2"
    msg_dir = v2 / "messages"
    ack_dir = v2 / "acks"
    task_dir = v2 / "tasks"
    proj_dir = v2 / "projects"

    messages: list[tuple[Path, dict]] = []
    seen_msg_ids: dict[str, Path] = {}

    if msg_dir.exists():
        for path in sorted(msg_dir.rglob("*.json")):
            rel = path.relative_to(root)
            data = json.loads(path.read_text(encoding="utf-8"))
            req = {
                "schema_version", "id", "thread_id", "project", "type", "created_at",
                "identity", "to", "ref_sha", "reply_to", "summary", "body", "approval",
            }
            opt = {"batch_items", "erp_refs"}
            if not isinstance(data, dict) or not req.issubset(data.keys()) or not set(data.keys()).issubset(req | opt):
                raise ValidationError(f"{rel}: claves V2 inválidas")
            if data["schema_version"] != "2.0":
                raise ValidationError(f"{rel}: schema_version debe ser '2.0'")
            mid = data["id"]
            if not isinstance(mid, str) or not MSG_ID_RE.match(mid) or path.name != f"{mid}.json":
                raise ValidationError(f"{rel}: id o nombre de archivo inválido")
            if mid in seen_msg_ids:
                raise ValidationError(f"{rel}: id duplicado {mid}")
            seen_msg_ids[mid] = rel

            tid = data["thread_id"]
            if not isinstance(tid, str) or not THREAD_ID_RE.match(tid) or path.parent.name != tid:
                raise ValidationError(f"{rel}: thread_id o carpeta inválida")
            if data["project"] not in PROJECTS:
                raise ValidationError(f"{rel}: project inválido: {data['project']!r}")
            if data["type"] not in V2_MSG_TYPES:
                raise ValidationError(f"{rel}: type inválido: {data['type']!r}")
            if not isinstance(data["created_at"], str) or not ISO_DT_RE.match(data["created_at"]):
                raise ValidationError(f"{rel}: created_at inválido")
            _check_identity(data["identity"], str(rel))
            to = data["to"]
            if not isinstance(to, list) or not to or any(x not in RECIPIENTS for x in to):
                raise ValidationError(f"{rel}: 'to' inválido")
            if data["ref_sha"] is not None and (not isinstance(data["ref_sha"], str) or not SHA_RE.match(data["ref_sha"])):
                raise ValidationError(f"{rel}: ref_sha inválido")
            if data["reply_to"] is not None:
                rt = data["reply_to"]
                if not isinstance(rt, dict) or set(rt.keys()) != {"message_id", "ref_sha"}:
                    raise ValidationError(f"{rel}: reply_to inválido")
                if not MSG_ID_RE.match(str(rt["message_id"])) or not SHA_RE.match(str(rt["ref_sha"])):
                    raise ValidationError(f"{rel}: reply_to campos inválidos")
            if not isinstance(data["summary"], str) or not (1 <= len(data["summary"]) <= 240):
                raise ValidationError(f"{rel}: summary debe tener 1..240 caracteres")
            if not isinstance(data["body"], str) or not data["body"].strip():
                raise ValidationError(f"{rel}: body vacío")
            if data["type"] == "batch":
                items = data.get("batch_items")
                if not isinstance(items, list) or not items:
                    raise ValidationError(f"{rel}: type=batch requiere batch_items no vacío")
                for idx, it in enumerate(items):
                    if not isinstance(it, dict) or not {"item_id", "to", "summary"}.issubset(it.keys()):
                        raise ValidationError(f"{rel}: batch_items[{idx}] inválido")
                    if any(x not in RECIPIENTS for x in it["to"]):
                        raise ValidationError(f"{rel}: batch_items[{idx}].to inválido")
            if "erp_refs" in data:
                _check_erp_refs(data["erp_refs"], str(rel))
            _check_approval(data["approval"], str(rel))
            messages.append((path, data))

    for path, data in messages:
        if data["reply_to"] is not None and data["reply_to"]["message_id"] not in seen_msg_ids:
            raise ValidationError(f"{path.relative_to(root)}: reply_to inexistente {data['reply_to']['message_id']}")

    acks: list[tuple[Path, dict]] = []
    if ack_dir.exists():
        for path in sorted(ack_dir.rglob("*.json")):
            rel = path.relative_to(root)
            data = json.loads(path.read_text(encoding="utf-8"))
            req = {"schema_version", "message_id", "observed_sha", "status", "created_at", "identity"}
            if not isinstance(data, dict) or not req.issubset(data.keys()) or not set(data.keys()).issubset(req | {"note"}):
                raise ValidationError(f"{rel}: claves ACK inválidas")
            if data["schema_version"] not in {"1.0", "1.1", "2.0"}:
                raise ValidationError(f"{rel}: schema_version ACK inválido")
            if data["message_id"] not in seen_msg_ids or path.parent.name != data["message_id"]:
                raise ValidationError(f"{rel}: message_id inválido o carpeta no coincide")
            ident = _check_identity(data["identity"], str(rel))
            if path.name != f"{ident['claimed_agent']}.json":
                raise ValidationError(f"{rel}: nombre ACK debe ser {ident['claimed_agent']}.json")
            if not SHA_RE.match(str(data["observed_sha"])) or data["status"] not in ACK_STATUSES:
                raise ValidationError(f"{rel}: observed_sha o status inválido")
            if not ISO_DT_RE.match(str(data["created_at"])):
                raise ValidationError(f"{rel}: created_at inválido")
            acks.append((path, data))

    tasks: list[tuple[Path, dict]] = []
    seen_task_ids: dict[str, dict] = {}
    if task_dir.exists():
        for path in sorted(task_dir.glob("*.json")):
            rel = path.relative_to(root)
            data = json.loads(path.read_text(encoding="utf-8"))
            req = {
                "schema_version", "id", "project", "title", "priority", "status",
                "owner", "depends_on", "checkpoint", "erp_refs", "updated_at", "identity", "approval",
            }
            if not isinstance(data, dict) or set(data.keys()) != req:
                raise ValidationError(f"{rel}: claves de tarea inválidas")
            if data["schema_version"] != "2.0":
                raise ValidationError(f"{rel}: schema_version debe ser '2.0'")
            tid = data["id"]
            if not isinstance(tid, str) or not TASK_ID_RE.match(tid) or path.name != f"{tid}.json":
                raise ValidationError(f"{rel}: id de tarea o nombre de archivo inválido")
            if data["project"] not in PROJECTS:
                raise ValidationError(f"{rel}: project inválido")
            if data["priority"] not in TASK_PRIORITIES or data["status"] not in TASK_STATUSES:
                raise ValidationError(f"{rel}: priority o status inválido")
            if data["owner"] not in RECIPIENTS:
                raise ValidationError(f"{rel}: owner inválido")
            if not isinstance(data["depends_on"], list):
                raise ValidationError(f"{rel}: depends_on debe ser lista")
            cp = data["checkpoint"]
            if not isinstance(cp, dict) or set(cp.keys()) != {"last_step", "resume_hint", "branch_or_pr", "paused_reason", "updated_at"}:
                raise ValidationError(f"{rel}: checkpoint inválido")
            if cp["paused_reason"] not in PAUSED_REASONS or not ISO_DT_RE.match(str(cp["updated_at"])):
                raise ValidationError(f"{rel}: checkpoint.paused_reason o updated_at inválido")
            _check_erp_refs(data["erp_refs"], str(rel))
            if not ISO_DT_RE.match(str(data["updated_at"])):
                raise ValidationError(f"{rel}: updated_at inválido")
            _check_identity(data["identity"], str(rel))
            _check_approval(data["approval"], str(rel))
            seen_task_ids[tid] = data
            tasks.append((path, data))

    for path, data in tasks:
        tid = data["id"]
        for dep in data["depends_on"]:
            if dep == tid:
                raise ValidationError(f"{path.relative_to(root)}: autodependencia en {tid}")
            if dep not in seen_task_ids:
                raise ValidationError(f"{path.relative_to(root)}: dependencia inexistente {dep}")

    # Detección de dependencias circulares (DFS 3 colores: 0=no visitado, 1=en curso, 2=cerrado)
    visit_state: dict[str, int] = {}

    def _dfs_cycle(node: str, trail: list[str]) -> None:
        state = visit_state.get(node, 0)
        if state == 1:
            raise ValidationError("Dependencia circular detectada en tareas: " + " -> ".join(trail + [node]))
        if state == 2:
            return
        visit_state[node] = 1
        for dep in seen_task_ids[node]["depends_on"]:
            _dfs_cycle(dep, trail + [node])
        visit_state[node] = 2

    for tid in sorted(seen_task_ids):
        if visit_state.get(tid, 0) == 0:
            _dfs_cycle(tid, [])

    projects: list[tuple[Path, dict]] = []
    if proj_dir.exists():
        for path in sorted(proj_dir.glob("*.json")):
            rel = path.relative_to(root)
            data = json.loads(path.read_text(encoding="utf-8"))
            req = {"schema_version", "project", "repo", "summary", "decisions", "updated_at"}
            if not isinstance(data, dict) or set(data.keys()) != req:
                raise ValidationError(f"{rel}: claves de proyecto inválidas")
            if data["schema_version"] != "2.0" or data["project"] not in PROJECTS or path.stem != data["project"]:
                raise ValidationError(f"{rel}: project o nombre de archivo inválido")
            if not isinstance(data["repo"], str) or not data["repo"].strip():
                raise ValidationError(f"{rel}: repo vacío")
            if not isinstance(data["decisions"], list):
                raise ValidationError(f"{rel}: decisions debe ser lista")
            for d in data["decisions"]:
                if not isinstance(d, dict) or set(d.keys()) != {"id", "title", "status", "ref_msg"}:
                    raise ValidationError(f"{rel}: decisión inválida")
                if not ADR_ID_RE.match(str(d["id"])) or d["status"] not in ADR_STATUSES:
                    raise ValidationError(f"{rel}: ADR id o status inválido")
                ref_msg = d["ref_msg"]
                if d["status"] == "accepted" and not ref_msg:
                    raise ValidationError(f"{rel}: decisión aceptada ({d['id']}) requiere ref_msg válido")
                if ref_msg is not None and ref_msg not in seen_msg_ids:
                    raise ValidationError(f"{rel}: decisión {d['id']} referencia mensaje inexistente {ref_msg}")
            if not ISO_DT_RE.match(str(data["updated_at"])):
                raise ValidationError(f"{rel}: updated_at inválido")
            projects.append((path, data))

    return messages, acks, tasks, projects


def build_v2_outputs(
    root: Path,
    messages: list[tuple[Path, dict]],
    acks: list[tuple[Path, dict]],
    tasks: list[tuple[Path, dict]],
    projects: list[tuple[Path, dict]],
) -> dict[str, str]:
    ack_map: dict[str, dict[str, dict]] = {}
    for _, ack in acks:
        ack_map.setdefault(ack["message_id"], {})[ack["identity"]["claimed_agent"]] = {
            "status": ack["status"],
            "observed_sha": ack["observed_sha"],
            "created_at": ack["created_at"],
        }

    ordered_msgs = sorted(messages, key=lambda item: (item[1]["created_at"], item[1]["id"]))
    entries = []
    for path, m in ordered_msgs:
        mid = m["id"]
        msg_acks = ack_map.get(mid, {})
        entries.append({
            "id": mid,
            "thread_id": m["thread_id"],
            "project": m["project"],
            "type": m["type"],
            "from": m["identity"]["claimed_agent"],
            "authenticated_principal": m["identity"]["authenticated_principal"],
            "to": m["to"],
            "created_at": m["created_at"],
            "reply_to": m["reply_to"]["message_id"] if m["reply_to"] else None,
            "approval_required": m["approval"]["required"],
            "approval_verified": False,
            "summary": m["summary"],
            "path": path.relative_to(root).as_posix(),
            "ack_status": "recorded" if msg_acks else "unknown",
            "acks": msg_acks,
        })

    task_summaries = [
        {
            "id": t["id"],
            "project": t["project"],
            "title": t["title"],
            "priority": t["priority"],
            "status": t["status"],
            "owner": t["owner"],
            "depends_on": t["depends_on"],
            "paused_reason": t["checkpoint"]["paused_reason"],
            "path": p.relative_to(root).as_posix(),
        }
        for p, t in sorted(tasks, key=lambda item: (item[1]["priority"], item[1]["id"]))
    ]

    shards: dict[str, list[dict]] = {}
    for e in entries:
        shards.setdefault(e["created_at"][:7], []).append(e)
    shard_paths = [f"protocol/v2/feed/{m}.json" for m in sorted(shards.keys())]
    latest_entries = entries[-LATEST_FEED_LIMIT:]
    head = entries[-1] if entries else None

    outputs: dict[str, str] = {}
    for month, shard_entries in sorted(shards.items()):
        outputs[f"protocol/v2/feed/{month}.json"] = json.dumps({
            "schema_version": "2.0",
            "shard": month,
            "message_count": len(shard_entries),
            "messages": shard_entries,
        }, indent=2, ensure_ascii=False) + "\n"

    outputs["protocol/v2/feed/latest.json"] = json.dumps({
        "schema_version": "2.0",
        "generated_by": "tools/validate_v2.py",
        "limit": LATEST_FEED_LIMIT,
        "total_messages": len(entries),
        "head_message_id": head["id"] if head else None,
        "head_created_at": head["created_at"] if head else None,
        "shards": shard_paths,
        "messages": latest_entries,
    }, indent=2, ensure_ascii=False) + "\n"

    outputs["protocol/v2/index.json"] = json.dumps({
        "schema_version": "2.0",
        "generated_by": "tools/validate_v2.py",
        "latest_feed": "protocol/v2/feed/latest.json",
        "shards": shard_paths,
        "message_count": len(entries),
        "task_count": len(task_summaries),
        "project_count": len(projects),
        "projects": [
            {
                "project": p["project"],
                "decisions": [
                    {
                        "id": d["id"],
                        "status": d["status"],
                        "ref_msg": d["ref_msg"],
                        "decision_verified": False,
                    }
                    for d in p["decisions"]
                ],
            }
            for _, p in projects
        ],
        "tasks": task_summaries,
        "messages": latest_entries,
    }, indent=2, ensure_ascii=False) + "\n"

    for recipient in sorted(RECIPIENTS):
        pending_msgs = [
            e for e in entries
            if recipient in e["to"] and recipient not in e["acks"]
        ]
        assigned_tasks = [
            t for t in task_summaries
            if t["owner"] == recipient and t["status"] != "done"
        ]
        outputs[f"protocol/v2/inboxes/{recipient}.json"] = json.dumps({
            "schema_version": "2.0",
            "agent": recipient,
            "head_message_id": head["id"] if head else None,
            "pending_message_count": len(pending_msgs),
            "assigned_task_count": len(assigned_tasks),
            "pending_messages": pending_msgs,
            "assigned_tasks": assigned_tasks,
        }, indent=2, ensure_ascii=False) + "\n"

    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valida Protocolo V2 y genera bandejas/feeds")
    parser.add_argument("--root", default=".", help="Raíz del repositorio")
    parser.add_argument("--base-ref", default="", help="Ref/SHA base para validar política Git V2")
    parser.add_argument("--build-index", action="store_true", help="Regenerar feeds, index.json y bandejas V2")
    parser.add_argument("--check-index", action="store_true", help="Verificar feeds, index.json y bandejas V2")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    base_ref = args.base_ref.strip() or None
    try:
        msgs, acks, tasks, projects = validate_v2(root, base_ref=base_ref)
    except ValidationError as err:
        print(f"ERROR V2: {err}", file=sys.stderr)
        return 1

    outputs = build_v2_outputs(root, msgs, acks, tasks, projects)
    if args.build_index:
        write_outputs(root, outputs)
        print(f"OK V2: {len(msgs)} mensajes, {len(acks)} ACKs, {len(tasks)} tareas, {len(projects)} proyectos; bandejas actualizadas")
        return 0

    if args.check_index:
        try:
            verify_outputs(root, outputs)
        except ValidationError as err:
            print(f"ERROR V2: {err}", file=sys.stderr)
            return 1

    print(f"OK V2: {len(msgs)} mensajes, {len(acks)} ACKs, {len(tasks)} tareas, {len(projects)} proyectos válidos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
