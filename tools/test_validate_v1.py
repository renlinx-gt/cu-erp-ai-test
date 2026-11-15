import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from validate_v1 import LATEST_FEED_LIMIT, ValidationError, build_feeds, validate_repo


def make_msg(msg_id="MSG-20261115-GEMINI-001", thread_id="THR-TEST", hab=None, created="2026-11-15T17:25:00Z"):
    return {
        "schema_version": "1.1",
        "id": msg_id,
        "thread_id": thread_id,
        "type": "propuesta",
        "created_at": created,
        "identity": {
            "claimed_agent": "gemini",
            "authenticated_principal": "renlinx-gt",
        },
        "to": ["chatgpt", "claude"],
        "ref_sha": "d96667a171c93dc51570b57cd5d6c6e4e20e44e1",
        "reply_to": None,
        "summary": "Propuesta de prueba",
        "body": "Contenido de prueba",
        "approval": {
            "required": True,
            "human_approved_by": hab,
        },
    }


def git(root: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return res.stdout.strip()


class TestProtocolV11(unittest.TestCase):
    def test_valid_message_ack_and_unverified_approval_flag(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mdir = root / "protocol" / "v1" / "messages" / "THR-TEST"
            mdir.mkdir(parents=True)
            (mdir / "MSG-20261115-GEMINI-001.json").write_text(
                json.dumps(make_msg(hab="renlinx-gt")), encoding="utf-8"
            )

            msgs, acks = validate_repo(root)
            outputs = build_feeds(root, msgs, acks)
            latest = json.loads(outputs["protocol/v1/feed/latest.json"])
            self.assertEqual(latest["total_messages"], 1)
            self.assertFalse(latest["messages"][0]["approval_verified"])
            self.assertEqual(latest["messages"][0]["ack_status"], "unknown")

    def test_rejects_ai_self_approval(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mdir = root / "protocol" / "v1" / "messages" / "THR-TEST"
            mdir.mkdir(parents=True)
            (mdir / "MSG-20261115-GEMINI-001.json").write_text(
                json.dumps(make_msg(hab="chatgpt")), encoding="utf-8"
            )
            with self.assertRaises(ValidationError):
                validate_repo(root)

    def test_immutability_rejects_message_modify_delete_and_ack_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            git(root, "init")
            git(root, "config", "user.name", "tester")
            git(root, "config", "user.email", "tester@example.com")

            mdir = root / "protocol" / "v1" / "messages" / "THR-TEST"
            mdir.mkdir(parents=True)
            msg_path = mdir / "MSG-20261115-GEMINI-001.json"
            msg_path.write_text(json.dumps(make_msg()), encoding="utf-8")

            adir = root / "protocol" / "v1" / "acks" / "MSG-20261115-GEMINI-001"
            adir.mkdir(parents=True)
            ack_path = adir / "chatgpt.json"
            ack_path.write_text(json.dumps({
                "schema_version": "1.1",
                "message_id": "MSG-20261115-GEMINI-001",
                "observed_sha": "d96667a171c93dc51570b57cd5d6c6e4e20e44e1",
                "status": "read",
                "created_at": "2026-11-15T17:26:00Z",
                "identity": {"claimed_agent": "chatgpt", "authenticated_principal": "renlinx-gt"},
            }), encoding="utf-8")

            git(root, "add", ".")
            git(root, "commit", "-m", "base")
            base_sha = git(root, "rev-parse", "HEAD")

            # Adding a second message is allowed
            msg2_path = mdir / "MSG-20261115-CLAUDE-002.json"
            msg2_path.write_text(json.dumps(make_msg("MSG-20261115-CLAUDE-002")), encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-m", "add msg2")
            validate_repo(root, base_ref=base_sha)

            # Modifying original message must fail
            m1 = make_msg()
            m1["body"] = "alterado"
            msg_path.write_text(json.dumps(m1), encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-m", "modify msg1")
            with self.assertRaises(ValidationError):
                validate_repo(root, base_ref=base_sha)

            # Reset and test ACK overwrite failure
            git(root, "reset", "--hard", base_sha)
            ack_path.write_text(json.dumps({
                "schema_version": "1.1",
                "message_id": "MSG-20261115-GEMINI-001",
                "observed_sha": "d96667a171c93dc51570b57cd5d6c6e4e20e44e1",
                "status": "answered",
                "created_at": "2026-11-15T17:30:00Z",
                "identity": {"claimed_agent": "chatgpt", "authenticated_principal": "renlinx-gt"},
            }), encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-m", "overwrite ack")
            with self.assertRaises(ValidationError):
                validate_repo(root, base_ref=base_sha)

    def test_incremental_latest_feed_limit_and_shards(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mdir = root / "protocol" / "v1" / "messages" / "THR-TEST"
            mdir.mkdir(parents=True)
            total = LATEST_FEED_LIMIT + 5
            for i in range(1, total + 1):
                mid = f"MSG-20261115-GEMINI-{i:03d}"
                ts = f"2026-11-15T17:{i:02d}:00Z"
                (mdir / f"{mid}.json").write_text(json.dumps(make_msg(mid, created=ts)), encoding="utf-8")

            msgs, acks = validate_repo(root)
            outputs = build_feeds(root, msgs, acks)
            latest = json.loads(outputs["protocol/v1/feed/latest.json"])
            self.assertEqual(latest["total_messages"], total)
            self.assertEqual(len(latest["messages"]), LATEST_FEED_LIMIT)
            self.assertEqual(latest["head_message_id"], f"MSG-20261115-GEMINI-{total:03d}")
            self.assertIn("protocol/v1/feed/2026-11.json", outputs)


if __name__ == "__main__":
    unittest.main()


class TestConcurrentWrites(unittest.TestCase):
    def test_concurrent_agent_writes_resolve_without_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            base_dir = Path(td)
            bare = base_dir / "remote.git"
            subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)

            seed = base_dir / "seed"
            subprocess.run(["git", "clone", str(bare), str(seed)], check=True, capture_output=True)
            git(seed, "checkout", "-b", "main")
            git(seed, "config", "user.name", "seed")
            git(seed, "config", "user.email", "seed@example.com")
            mdir = seed / "protocol" / "v1" / "messages" / "THR-TEST"
            mdir.mkdir(parents=True)
            (mdir / "MSG-20261115-GEMINI-001.json").write_text(json.dumps(make_msg()), encoding="utf-8")
            git(seed, "add", ".")
            git(seed, "commit", "-m", "seed")
            git(seed, "push", "-u", "origin", "main")
            base_sha = git(seed, "rev-parse", "HEAD")

            # Dos agentes clonan el mismo SHA base simultáneamente
            clone_a = base_dir / "agent_chatgpt"
            clone_b = base_dir / "agent_claude"
            subprocess.run(["git", "clone", "--branch", "main", str(bare), str(clone_a)], check=True, capture_output=True)
            subprocess.run(["git", "clone", "--branch", "main", str(bare), str(clone_b)], check=True, capture_output=True)

            for c, agent in [(clone_a, "chatgpt"), (clone_b, "claude")]:
                git(c, "config", "user.name", agent)
                git(c, "config", "user.email", f"{agent}@example.com")

            # Agente A escribe y publica primero
            m_a = make_msg("MSG-20261115-CHATGPT-002", created="2026-11-15T17:26:00Z")
            m_a["identity"]["claimed_agent"] = "chatgpt"
            (clone_a / "protocol" / "v1" / "messages" / "THR-TEST" / "MSG-20261115-CHATGPT-002.json").write_text(
                json.dumps(m_a), encoding="utf-8"
            )
            git(clone_a, "add", ".")
            git(clone_a, "commit", "-m", "msg chatgpt")
            git(clone_a, "push", "origin", "main")

            # Agente B escribe desde el mismo SHA base; su push directo debe fallar, pero rebase es limpio
            m_b = make_msg("MSG-20261115-CLAUDE-003", created="2026-11-15T17:26:05Z")
            m_b["identity"]["claimed_agent"] = "claude"
            (clone_b / "protocol" / "v1" / "messages" / "THR-TEST" / "MSG-20261115-CLAUDE-003.json").write_text(
                json.dumps(m_b), encoding="utf-8"
            )
            git(clone_b, "add", ".")
            git(clone_b, "commit", "-m", "msg claude")
            failed_push = subprocess.run(["git", "-C", str(clone_b), "push", "origin", "main"], capture_output=True)
            self.assertNotEqual(failed_push.returncode, 0)

            # Rebase sin conflictos + validación de inmutabilidad + feed consolidado
            git(clone_b, "pull", "--rebase", "origin", "main")
            git(clone_b, "push", "origin", "main")
            msgs, acks = validate_repo(clone_b, base_ref=base_sha)
            outputs = build_feeds(clone_b, msgs, acks)
            latest = json.loads(outputs["protocol/v1/feed/latest.json"])
            self.assertEqual(latest["total_messages"], 3)
            ids = {m["id"] for m in latest["messages"]}
            self.assertEqual(ids, {"MSG-20261115-GEMINI-001", "MSG-20261115-CHATGPT-002", "MSG-20261115-CLAUDE-003"})
