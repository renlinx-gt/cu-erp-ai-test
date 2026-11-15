import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from validate_v1 import LATEST_FEED_LIMIT, ValidationError
from validate_v2 import build_v2_outputs, validate_v2


def make_v2_msg(mid="MSG-20261115-GEMINI-101", msg_type="propuesta", to=None):
    data = {
        "schema_version": "2.0",
        "id": mid,
        "thread_id": "THR-V2-TEST",
        "project": "cu_gensite",
        "type": msg_type,
        "created_at": "2026-11-15T18:00:00Z",
        "identity": {"claimed_agent": "gemini", "authenticated_principal": "renlinx-gt"},
        "to": to or ["chatgpt", "codex"],
        "ref_sha": "f01d957da88ab64e0c0817de9b4dcfd2dbe42c9c",
        "reply_to": None,
        "summary": "Mensaje V2 de prueba",
        "body": "Contenido de prueba V2",
        "erp_refs": [{"repo": "https://github.com/renlinx-gt/cu_gensite", "ref": "main", "path": "README.md"}],
        "approval": {"required": False, "human_approved_by": None},
    }
    if msg_type == "batch":
        data["batch_items"] = [
            {"item_id": "B1", "to": ["chatgpt"], "task_id": "TSK-CU-GENSITE-001", "summary": "Sub-item 1"}
        ]
    return data


def make_v2_task(tid="TSK-CU-GENSITE-001", depends_on=None, owner="codex", paused="none"):
    return {
        "schema_version": "2.0",
        "id": tid,
        "project": "cu_gensite",
        "title": "Prueba tarea V2",
        "priority": "P1",
        "status": "in_progress",
        "owner": owner,
        "depends_on": depends_on or [],
        "checkpoint": {
            "last_step": "schema_ready",
            "resume_hint": "Continuar con pruebas",
            "branch_or_pr": None,
            "paused_reason": paused,
            "updated_at": "2026-11-15T18:00:00Z",
        },
        "erp_refs": [],
        "updated_at": "2026-11-15T18:00:00Z",
        "identity": {"claimed_agent": "gemini", "authenticated_principal": "renlinx-gt"},
        "approval": {"required": False, "human_approved_by": None},
    }


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


class TestProtocolV2(unittest.TestCase):
    def test_inboxes_batch_tasks_and_projects(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "protocol/v2/messages/THR-V2-TEST").mkdir(parents=True)
            (root / "protocol/v2/tasks").mkdir(parents=True)
            (root / "protocol/v2/projects").mkdir(parents=True)

            (root / "protocol/v2/messages/THR-V2-TEST/MSG-20261115-GEMINI-101.json").write_text(
                json.dumps(make_v2_msg(msg_type="batch")), encoding="utf-8"
            )
            (root / "protocol/v2/tasks/TSK-CU-GENSITE-001.json").write_text(
                json.dumps(make_v2_task(owner="codex", paused="quota_limit")), encoding="utf-8"
            )
            (root / "protocol/v2/projects/cu_gensite.json").write_text(json.dumps({
                "schema_version": "2.0",
                "project": "cu_gensite",
                "repo": "https://github.com/renlinx-gt/cu_gensite",
                "summary": "Configurador empresarial ERPNext",
                "decisions": [{"id": "ADR-20261115-001", "title": "Usar perfiles", "status": "proposed", "ref_msg": "MSG-20261115-GEMINI-101"}],
                "updated_at": "2026-11-15T18:00:00Z",
            }), encoding="utf-8")

            msgs, acks, tasks, projects = validate_v2(root)
            outputs = build_v2_outputs(root, msgs, acks, tasks, projects)
            codex_inbox = json.loads(outputs["protocol/v2/inboxes/codex.json"])
            self.assertEqual(codex_inbox["pending_message_count"], 1)
            self.assertEqual(codex_inbox["assigned_task_count"], 1)
            self.assertEqual(codex_inbox["assigned_tasks"][0]["paused_reason"], "quota_limit")

    def test_rejects_missing_task_dependency_and_message_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "protocol/v2/tasks").mkdir(parents=True)
            (root / "protocol/v2/tasks/TSK-CU-GENSITE-001.json").write_text(
                json.dumps(make_v2_task(depends_on=["TSK-CU-FEL-999"])), encoding="utf-8"
            )
            with self.assertRaises(ValidationError):
                validate_v2(root)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            git(root, "init")
            git(root, "config", "user.name", "t")
            git(root, "config", "user.email", "t@example.com")
            (root / "protocol/v2/messages/THR-V2-TEST").mkdir(parents=True)
            mp = root / "protocol/v2/messages/THR-V2-TEST/MSG-20261115-GEMINI-101.json"
            mp.write_text(json.dumps(make_v2_msg()), encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-m", "base")
            base = git(root, "rev-parse", "HEAD")

            m = make_v2_msg()
            m["body"] = "editado"
            mp.write_text(json.dumps(m), encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-m", "mutate")
            with self.assertRaises(ValidationError):
                validate_v2(root, base_ref=base)


    def test_inbox_retains_all_pending_messages_beyond_latest_limit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mdir = root / "protocol/v2/messages/THR-V2-TEST"
            mdir.mkdir(parents=True)
            total = LATEST_FEED_LIMIT + 5
            for i in range(1, total + 1):
                mid = f"MSG-20261115-GEMINI-{i:03d}"
                m = make_v2_msg(mid=mid, to=["claude"])
                m["created_at"] = f"2026-11-15T18:{i:02d}:00Z"
                (mdir / f"{mid}.json").write_text(json.dumps(m), encoding="utf-8")

            msgs, acks, tasks, projects = validate_v2(root)
            outputs = build_v2_outputs(root, msgs, acks, tasks, projects)
            latest = json.loads(outputs["protocol/v2/feed/latest.json"])
            claude_inbox = json.loads(outputs["protocol/v2/inboxes/claude.json"])
            self.assertEqual(len(latest["messages"]), LATEST_FEED_LIMIT)
            self.assertEqual(claude_inbox["pending_message_count"], total)

    def test_rejects_accepted_decision_without_valid_ref_msg_and_marks_unverified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "protocol/v2/projects").mkdir(parents=True)
            (root / "protocol/v2/projects/cu_gensite.json").write_text(json.dumps({
                "schema_version": "2.0",
                "project": "cu_gensite",
                "repo": "https://github.com/renlinx-gt/cu_gensite",
                "summary": "Test",
                "decisions": [{"id": "ADR-20261115-001", "title": "Sin ref", "status": "accepted", "ref_msg": None}],
                "updated_at": "2026-11-15T18:00:00Z",
            }), encoding="utf-8")
            with self.assertRaises(ValidationError):
                validate_v2(root)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "protocol/v2/messages/THR-V2-TEST").mkdir(parents=True)
            (root / "protocol/v2/projects").mkdir(parents=True)
            (root / "protocol/v2/messages/THR-V2-TEST/MSG-20261115-GEMINI-101.json").write_text(
                json.dumps(make_v2_msg()), encoding="utf-8"
            )
            (root / "protocol/v2/projects/cu_gensite.json").write_text(json.dumps({
                "schema_version": "2.0",
                "project": "cu_gensite",
                "repo": "https://github.com/renlinx-gt/cu_gensite",
                "summary": "Test",
                "decisions": [{"id": "ADR-20261115-001", "title": "Con ref", "status": "accepted", "ref_msg": "MSG-20261115-GEMINI-101"}],
                "updated_at": "2026-11-15T18:00:00Z",
            }), encoding="utf-8")
            msgs, acks, tasks, projects = validate_v2(root)
            outputs = build_v2_outputs(root, msgs, acks, tasks, projects)
            idx = json.loads(outputs["protocol/v2/index.json"])
            self.assertFalse(idx["projects"][0]["decisions"][0]["decision_verified"])

    def test_rejects_circular_task_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "protocol/v2/tasks").mkdir(parents=True)
            (root / "protocol/v2/tasks/TSK-CU-A-001.json").write_text(
                json.dumps(make_v2_task("TSK-CU-A-001", depends_on=["TSK-CU-B-002"])), encoding="utf-8"
            )
            (root / "protocol/v2/tasks/TSK-CU-B-002.json").write_text(
                json.dumps(make_v2_task("TSK-CU-B-002", depends_on=["TSK-CU-C-003"])), encoding="utf-8"
            )
            (root / "protocol/v2/tasks/TSK-CU-C-003.json").write_text(
                json.dumps(make_v2_task("TSK-CU-C-003", depends_on=["TSK-CU-A-001"])), encoding="utf-8"
            )
            with self.assertRaises(ValidationError):
                validate_v2(root)


if __name__ == "__main__":
    unittest.main()
