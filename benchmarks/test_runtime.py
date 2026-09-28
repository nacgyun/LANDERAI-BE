"""Offline regression tests: no AWS or LLM requests."""
import os
import unittest
from unittest.mock import patch, Mock

os.environ.update({"APP_ENV": "benchmark", "BENCHMARK_GROUP": "a",
                   "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                   "AWS_EC2_METADATA_DISABLED": "true"})

from app.benchmark import runtime
from app.benchmark import api
from fastapi import HTTPException
from app.schemas.request import LandingPageCreateRequest


class RuntimeTests(unittest.TestCase):
    def test_monolithic_runs_a_then_b_and_persists_both(self):
        calls = []
        def embedding(event, context):
            calls.append("embedding")
            return event
        def design(event, context):
            calls.append("design")
            return {**event, "variants": ["A", "B"], "design_plan_json": "{}"}
        def variant(event, context):
            calls.append(event["variant"])
            return {"variant": event["variant"]}
        def persist(event, context):
            calls.append("persist")
            self.assertEqual(event["generated_variants"], [{"variant": "A"}, {"variant": "B"}])
        with patch.multiple(runtime, embedding_handler=embedding, design_handler=design,
                            variant_handler=variant, persist_handler=persist):
            runtime.monolithic_handler({"request_id": "test"}, None)
        self.assertEqual(calls, ["embedding", "design", "A", "B", "persist"])

    def test_embedding_uses_live_rag_and_records_resolved_ids(self):
        request = {"request_id": "test", "industry": "cafe"}
        embedding = {
            "embedding": [1, 2], "embedding_model": "test-model",
            "embedding_input": "test-input", "embedding_input_tokens": 1,
        }
        examples = [{"source_request": {"request_id": "rag-1"}, "selected_design_plan": {}}]
        with patch.object(runtime.repo, "update_landing_page_request_state"), \
             patch.object(runtime.repo, "get_landing_page_request", return_value=request), \
             patch.object(runtime, "create_request_embedding", return_value=embedding), \
             patch.object(runtime, "query_nearest_design_plan_request_ids", return_value=["rag-1", "stale"]), \
             patch.object(runtime, "get_rag_design_plans_by_request_ids", return_value=[{"request_id": "rag-1"}]), \
             patch.object(runtime, "build_rag_examples", return_value=examples), \
             patch.object(runtime.repo, "save_landing_page_request_embedding"), \
             patch.object(runtime, "get_request_table") as table:
            result = runtime.embedding("test", {})
        values = table.return_value.update_item.call_args.kwargs["ExpressionAttributeValues"]
        self.assertEqual(values[":ids"], ["rag-1"])
        self.assertEqual(values[":candidate_ids"], ["rag-1", "stale"])
        self.assertEqual(result["benchmark_rag_json"], values[":examples"])

    def test_design_uses_rag_passed_by_previous_stage(self):
        request = {"request_id": "test"}
        rag_json = '[{"source_request":{"request_id":"rag-1"}}]'
        design_result = {
            "design_plan_json": "{}", "design_plan_input_tokens": 1,
            "design_plan_output_tokens": 1, "design_plan_estimated_cost": 0,
        }
        with patch.object(runtime.repo, "get_landing_page_request", return_value=request), \
             patch.object(runtime, "create_design_plan_with_mutation", return_value=design_result) as create, \
             patch.object(runtime.repo, "save_landing_page_design_plan"):
            runtime.design("test", {"benchmark_rag_json": rag_json})
        self.assertEqual(create.call_args.kwargs["rag_examples"][0]["source_request"]["request_id"], "rag-1")

    def test_failed_variant_does_not_persist_partial_result(self):
        with patch.object(runtime, "embedding_handler", return_value={"request_id": "test"}), \
             patch.object(runtime, "design_handler", return_value={"variants": ["A", "B"], "design_plan_json": "{}"}), \
             patch.object(runtime, "variant_handler", side_effect=[{"variant": "A"}, ValueError("failed")]), \
             patch.object(runtime, "persist_handler") as persist:
            with self.assertRaises(ValueError):
                runtime.monolithic_handler({"request_id": "test"}, None)
        persist.assert_not_called()

    def test_persist_rejects_incomplete_variants(self):
        with patch.object(runtime.repo, "save_landing_page_result") as save:
            with self.assertRaises(ValueError):
                runtime.persist("test", {"generated_variants": [{"variant": "A"}]})
        save.assert_not_called()

    def test_failed_stage_records_failure_and_reraises(self):
        with patch.object(runtime.repo, "mark_landing_page_request_failed") as fail:
            with self.assertRaises(ValueError):
                runtime.stage_entry("test_stage", {"request_id": "test"}, None,
                                    Mock(side_effect=ValueError("bad")))
        self.assertEqual(fail.call_args.kwargs["error_type"], "ValueError")

    def test_start_failure_is_not_reported_as_accepted(self):
        request = LandingPageCreateRequest(industry="cafe", sub_industry="cafe", target="all",
                                          style="simple", goal="visit", language="ko")
        with patch.dict(os.environ, {"BENCHMARK_GROUP": "b"}), \
             patch.object(api.repo, "save_landing_page_request"), \
             patch.object(api.repo, "mark_landing_page_request_failed") as fail, \
             patch.object(api.boto3, "client") as client:
            client.return_value.start_execution.side_effect = RuntimeError("no execution")
            with self.assertRaises(HTTPException) as error:
                api.create(request)
        self.assertEqual(error.exception.status_code, 503)
        fail.assert_called_once()

    def test_a_submits_monolithic_lambda_asynchronously(self):
        request = LandingPageCreateRequest(industry="cafe", sub_industry="cafe", target="all",
                                          style="simple", goal="visit", language="ko")
        with patch.dict(os.environ, {
            "BENCHMARK_GROUP": "a",
            "MONOLITHIC_WORKFLOW_FUNCTION_NAME": "benchmark-a-workflow",
        }), patch.object(api.repo, "save_landing_page_request"), \
             patch.object(api, "get_request_table"), \
             patch.object(api.boto3, "client") as client:
            client.return_value.invoke.return_value = {
                "StatusCode": 202, "ResponseMetadata": {"RequestId": "invoke-1"}
            }
            result = api.create(request)
        self.assertEqual(result["status"], "QUEUED")
        self.assertEqual(client.return_value.invoke.call_args.kwargs["InvocationType"], "Event")

    def test_lambda_timeout_is_visible_even_if_db_still_processing(self):
        with patch.object(api, "get_request_table") as table, patch.object(api.boto3, "client") as client:
            table.return_value.get_item.return_value = {"Item": {
                "request_id": "test", "status": "PROCESSING", "workflow_execution_arn": "arn:test",
            }}
            client.return_value.describe_execution.return_value = {"status": "TIMED_OUT"}
            self.assertEqual(api.status("test")["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
