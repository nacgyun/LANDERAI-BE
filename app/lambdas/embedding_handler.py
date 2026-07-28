from app.common.workflow_status import STATUS_PROCESSING, STEP_EMBEDDING
from app.lambdas.common import (
    decimal_to_float_list,
    log_workflow_event,
    mark_failed,
    now_iso,
)
from app.pipelines.landing_page.tasks.embedding import create_request_embedding
from app.repositories.request_repository import (
    get_landing_page_request,
    save_landing_page_request_embedding,
    update_landing_page_request_state,
)
from app.repositories.s3_vector_repository import query_nearest_design_plan_request_ids


def lambda_handler(event, context):
    request_id = event.get("request_id")
    try:
        if not request_id:
            raise ValueError("request_id is required.")

        log_workflow_event(
            step=STEP_EMBEDDING,
            request_id=request_id,
            message="started",
        )

        update_landing_page_request_state(
            request_id,
            status=STATUS_PROCESSING,
            current_step=STEP_EMBEDDING,
            progress=10,
            updated_at=now_iso(),
        )

        request_item = get_landing_page_request(request_id)
        if request_item is None:
            raise ValueError(f"LandingPageRequest not found: {request_id}")

        embedding_result = create_request_embedding(request_item)

        try:
            rag_request_ids = query_nearest_design_plan_request_ids(
                industry=request_item.get("industry"),
                embedding=decimal_to_float_list(embedding_result["embedding"]),
                top_k=3,
                exclude_request_id=request_id,
            )
        except Exception as rag_err:
            print(f"[RAG] nearest design plans lookup skipped: {rag_err}")
            rag_request_ids = []

        save_landing_page_request_embedding(
            request_id,
            embedding=embedding_result["embedding"],
            embedding_model=embedding_result["embedding_model"],
            embedding_input=embedding_result["embedding_input"],
            embedding_input_tokens=embedding_result["embedding_input_tokens"],
            updated_at=now_iso(),
        )

        update_landing_page_request_state(
            request_id,
            status=STATUS_PROCESSING,
            current_step=STEP_EMBEDDING,
            progress=25,
            updated_at=now_iso(),
        )

        log_workflow_event(
            step=STEP_EMBEDDING,
            request_id=request_id,
            message="completed",
            rag_request_count=len(rag_request_ids),
            rag_request_ids=",".join(rag_request_ids),
        )

        return {
            "request_id": request_id,
            "rag_request_ids": rag_request_ids,
        }
    except Exception as err:
        mark_failed(request_id, STEP_EMBEDDING, err)
        raise
