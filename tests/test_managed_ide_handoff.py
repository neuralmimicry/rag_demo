from __future__ import annotations

import unittest
import hashlib

from refiner.runtime.managed_ide import build_handoff_payload, submit_handoff


class _Response:
    status_code = 202

    @staticmethod
    def json():
        return {
            "status": "queued",
            "handoffId": "12345678-1234-4234-8234-123456789abc",
            "conversationId": "12345678-1234-4234-8234-123456789abc",
            "browserUrl": "https://ide.example.test/?conversation=12345678-1234-4234-8234-123456789abc",
        }


class ManagedIdeHandoffTests(unittest.TestCase):
    def payload(self):
        return build_handoff_payload(
            job_id="a" * 32,
            repository="neuralmimicry/rag_demo",
            source_ref="refiner/a" * 1,
            source_sha="b" * 40,
            requirements="Fix the project acceptance criteria.",
            completion_summary={"needs_more_iterations": True},
            completion_reason="Requirements remain incomplete.",
        )

    def test_handoff_payload_is_stable_and_binds_the_exact_source(self):
        first = self.payload()
        second = self.payload()
        self.assertEqual(first, second)
        self.assertEqual(first["sourceSha"], "b" * 40)
        self.assertEqual(first["sourceRef"], "refiner/a")
        self.assertEqual(len(first["idempotencyKey"]), 64)
        self.assertEqual(first["promptSha256"], hashlib.sha256(first["prompt"].encode()).hexdigest())
        self.assertIn("Fix the project acceptance criteria.", first["prompt"])

    def test_rejects_main_branch_and_invalid_commit(self):
        with self.assertRaises(ValueError):
            build_handoff_payload(
                job_id="a" * 32,
                repository="neuralmimicry/rag_demo",
                source_ref="main",
                source_sha="b" * 40,
                requirements="request",
                completion_summary={},
                completion_reason="incomplete",
            )
        with self.assertRaises(ValueError):
            build_handoff_payload(
                job_id="a" * 32,
                repository="neuralmimicry/rag_demo",
                source_ref="refiner/job",
                source_sha="not-a-commit",
                requirements="request",
                completion_summary={},
                completion_reason="incomplete",
            )

    def test_posts_only_with_service_token_and_bounds_saved_result(self):
        calls = []

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            return _Response()

        payload = self.payload()
        result = submit_handoff(
            payload,
            enabled=True,
            endpoint="http://agentd.neuralmimicry-ide.svc.cluster.local:8080/internal/refiner/handoffs",
            service_token="s" * 40,
            post=fake_post,
        )
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["sourceSha"], payload["sourceSha"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["headers"]["Authorization"], f"Bearer {'s' * 40}")
        self.assertEqual(calls[0][1]["timeout"], (5, 155))

    def test_missing_token_and_disabled_switch_do_not_send(self):
        payload = self.payload()
        calls = []
        self.assertIsNone(
            submit_handoff(payload, enabled=False, endpoint="http://agentd.local/handoff", service_token="s" * 40,
                           post=lambda *_args, **_kwargs: calls.append(True))
        )
        result = submit_handoff(payload, enabled=True, endpoint="http://agentd.local/handoff", service_token="")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
