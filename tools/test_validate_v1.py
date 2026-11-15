import json
import tempfile
import unittest
from pathlib import Path

from validate_v1 import ValidationError, build_index_dict, validate_repo


def make_msg(msg_id="MSG-20261115-GEMINI-001", thread_id="THR-TEST", hab=None):
    return {
        "schema_version": "1.0",
        "id": msg_id,
        "thread_id": thread_id,
        "type": "propuesta",
        "created_at": "2026-11-15T17:25:00Z",
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


class TestProtocolV1(unittest.TestCase):
    def test_valid_message_and_optional_ack(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mdir = root / "protocol" / "v1" / "messages" / "THR-TEST"
            mdir.mkdir(parents=True)
            (mdir / "MSG-20261115-GEMINI-001.json").write_text(json.dumps(make_msg()), encoding="utf-8")

            # No ACKs yet: ack_status must be unknown (non-blocking)
            msgs, acks = validate_repo(root)
            idx = build_index_dict(root, msgs, acks)
            self.assertEqual(idx["message_count"], 1)
            self.assertEqual(idx["messages"][0]["ack_status"], "unknown")

            # Add ACK from chatgpt
            adir = root / "protocol" / "v1" / "acks" / "MSG-20261115-GEMINI-001"
            adir.mkdir(parents=True)
            (adir / "chatgpt.json").write_text(json.dumps({
                "schema_version": "1.0",
                "message_id": "MSG-20261115-GEMINI-001",
                "observed_sha": "d96667a171c93dc51570b57cd5d6c6e4e20e44e1",
                "status": "read",
                "created_at": "2026-11-15T17:26:00Z",
                "identity": {
                    "claimed_agent": "chatgpt",
                    "authenticated_principal": "renlinx-gt",
                },
            }), encoding="utf-8")

            msgs, acks = validate_repo(root)
            idx = build_index_dict(root, msgs, acks)
            self.assertEqual(idx["messages"][0]["ack_status"], "recorded")
            self.assertIn("chatgpt", idx["messages"][0]["acks"])

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

    def test_rejects_broken_reply_to(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mdir = root / "protocol" / "v1" / "messages" / "THR-TEST"
            mdir.mkdir(parents=True)
            m = make_msg()
            m["reply_to"] = {
                "message_id": "MSG-20261115-CLAUDE-999",
                "ref_sha": "d96667a171c93dc51570b57cd5d6c6e4e20e44e1",
            }
            (mdir / "MSG-20261115-GEMINI-001.json").write_text(json.dumps(m), encoding="utf-8")
            with self.assertRaises(ValidationError):
                validate_repo(root)


if __name__ == "__main__":
    unittest.main()
