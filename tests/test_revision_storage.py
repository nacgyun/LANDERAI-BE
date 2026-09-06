import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.repositories import request_repository, s3_repository
from app.services import revision_service
from app.lambdas import revision_worker_handler
from app.messaging import revision_publisher


class RevisionStorageTests(unittest.TestCase):
    def test_revision_s3_key_uses_immutable_revision_id(self):
        key = s3_repository.build_landing_page_revision_key(
            user_id="user/1", request_id="req/123", revision_id="rev/abc"
        )
        self.assertEqual(
            key,
            "landing-pages/user%2F1/req%2F123/revisions/rev%2Fabc/index.html",
        )

    def test_initial_revision_and_request_pointer_are_written_atomically(self):
        class FakeClient:
            def __init__(self):
                self.kwargs = None

            def transact_write_items(self, **kwargs):
                self.kwargs = kwargs

        client = FakeClient()
        request_table = SimpleNamespace(
            name="LandingRequest", meta=SimpleNamespace(client=client)
        )
        result_table = SimpleNamespace(name="LandingResult")

        with (
            patch.object(
                request_repository, "get_request_table", return_value=request_table
            ),
            patch.object(
                request_repository, "get_result_table", return_value=result_table
            ),
        ):
            request_repository.save_initial_revision_and_landing_page_variant_selection(
                "req_123",
                revision_item={
                    "result_id": "rev_123",
                    "item_type": "REVISION",
                    "revision_id": "rev_123",
                    "request_id": "req_123",
                    "source_revision_id": None,
                    "revision_prompt": None,
                    "source_type": "VARIANT",
                    "source_variant": "A",
                    "html_s3_bucket": "page-bucket",
                    "html_s3_key": "variants/A/index.html",
                    "status": "COMPLETED",
                    "created_at": "now",
                    "updated_at": "now",
                },
                chosen_variant="A",
                selected_design_plan_id="dp_123_A",
                design_plan_vector_store="S3_VECTOR",
                design_plan_vector_bucket="vector-bucket",
                design_plan_vector_index="vector-index",
                design_plan_vector_key="vector-key",
                design_plan_vector_uri="s3://vector-bucket/vector-key",
                selected_at="now",
                updated_at="now",
            )

        transaction_items = client.kwargs["TransactItems"]
        self.assertEqual(len(transaction_items), 2)
        self.assertEqual(transaction_items[0]["Put"]["TableName"], "LandingResult")
        self.assertEqual(transaction_items[1]["Update"]["TableName"], "LandingRequest")
        expression_values = transaction_items[1]["Update"][
            "ExpressionAttributeValues"
        ]
        self.assertEqual(expression_values[":latest_revision_id"], "rev_123")
        self.assertEqual(expression_values[":selection_status"], "SELECTED")

    def test_revision_processing_completes_and_advances_latest_pointer(self):
        generated = SimpleNamespace(title="Revised page", html="<html>revised</html>")
        with (
            patch.object(
                revision_service,
                "get_landing_page_revision",
                side_effect=[
                    {
                        "revision_id": "rev_new",
                        "request_id": "req_123",
                        "source_revision_id": "rev_source",
                        "revision_prompt": "Make the CTA blue",
                        "status": "QUEUED",
                    },
                    {
                        "revision_id": "rev_source",
                        "request_id": "req_123",
                        "status": "COMPLETED",
                        "html_s3_bucket": "page-bucket",
                        "html_s3_key": "source/index.html",
                    },
                ],
            ),
            patch.object(
                revision_service,
                "get_landing_page_request",
                return_value={"request_id": "req_123", "user_id": "user_1"},
            ),
            patch.object(
                revision_service,
                "get_landing_page_html",
                return_value="<html>source</html>",
            ),
            patch.object(
                revision_service,
                "revise_landing_page",
                return_value=(generated, 100, 200),
            ),
            patch.object(
                revision_service,
                "upload_landing_page_revision_html",
                return_value={
                    "html_s3_bucket": "page-bucket",
                    "html_s3_key": "revisions/rev_new/index.html",
                },
            ),
            patch.object(
                revision_service, "mark_landing_page_revision_processing"
            ) as processing,
            patch.object(revision_service, "complete_landing_page_revision") as complete,
            patch.object(revision_service, "mark_landing_page_revision_failed") as fail,
        ):
            revision_service.process_landing_page_revision("req_123", "rev_new")

        complete.assert_called_once()
        processing.assert_called_once()
        self.assertEqual(processing.call_args.args[0], "rev_new")
        self.assertEqual(complete.call_args.args[:2], ("req_123", "rev_new"))
        self.assertEqual(
            complete.call_args.kwargs["html_s3_key"],
            "revisions/rev_new/index.html",
        )
        fail.assert_not_called()

    def test_revision_processing_marks_failed_without_advancing_pointer(self):
        with (
            patch.object(
                revision_service,
                "get_landing_page_revision",
                return_value={
                    "revision_id": "rev_new",
                    "request_id": "req_123",
                    "source_revision_id": "rev_source",
                    "revision_prompt": "Change the CTA",
                    "status": "PROCESSING",
                },
            ),
            patch.object(
                revision_service,
                "get_landing_page_html",
                side_effect=AssertionError("source HTML must not be read"),
            ),
            patch.object(revision_service, "complete_landing_page_revision") as complete,
            patch.object(revision_service, "mark_landing_page_revision_failed") as fail,
        ):
            with self.assertRaises(ValueError):
                revision_service.process_landing_page_revision("req_123", "rev_new")

        complete.assert_not_called()
        fail.assert_called_once()
        self.assertEqual(fail.call_args.args[0], "rev_new")

    def test_revision_queue_message_contains_only_identifiers(self):
        fake_client = SimpleNamespace(
            send_message=lambda **kwargs: {"MessageId": "message_123"}
        )
        with (
            patch.object(
                revision_publisher.settings,
                "REVISION_QUEUE_URL",
                "https://sqs.example/revisions",
            ),
            patch.object(
                revision_publisher,
                "_get_sqs_client",
                return_value=fake_client,
            ),
            patch.object(fake_client, "send_message", wraps=fake_client.send_message) as send,
        ):
            message_id = revision_publisher.publish_revision_requested(
                request_id="req_123", revision_id="rev_123"
            )

        self.assertEqual(message_id, "message_123")
        body = __import__("json").loads(send.call_args.kwargs["MessageBody"])
        self.assertEqual(
            body,
            {
                "event_type": "LANDING_PAGE_REVISION_REQUESTED",
                "request_id": "req_123",
                "revision_id": "rev_123",
            },
        )

    def test_worker_marks_failed_only_on_final_receive(self):
        record = {
            "body": __import__("json").dumps(
                {
                    "event_type": "LANDING_PAGE_REVISION_REQUESTED",
                    "request_id": "req_123",
                    "revision_id": "rev_123",
                }
            ),
            "attributes": {"ApproximateReceiveCount": "3"},
        }
        with (
            patch.object(
                revision_worker_handler.settings,
                "REVISION_MAX_RECEIVE_COUNT",
                3,
            ),
            patch.object(
                revision_worker_handler,
                "process_landing_page_revision",
            ) as process,
        ):
            revision_worker_handler.lambda_handler({"Records": [record]}, None)

        process.assert_called_once_with(
            "req_123", "rev_123", mark_failed_on_error=True
        )


if __name__ == "__main__":
    unittest.main()
