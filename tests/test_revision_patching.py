import json
from types import SimpleNamespace

from app.revision_patching.hybrid import revise_landing_page_hybrid
from app.revision_patching.engine import PatchError
from app.schemas.landing_page import LandingPageGenerationResponse


def _response(payload: dict, *, prompt_tokens: int = 100, completion_tokens: int = 20):
    return SimpleNamespace(
        id="resp_test",
        model="test-model",
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
        choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content=json.dumps(payload), refusal=None),
        )],
    )


class _Completions:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


def _client(*responses):
    completions = _Completions(responses)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def _text_plan(selector="#title", expected_text="Old", value="New"):
    return {
        "version": "1",
        "patches": [{
            "op": "set_text",
            "selector": selector,
            "expected_count": 1,
            "expected_text": expected_text,
            "value": value,
        }],
    }


def test_patch_success_returns_patch_route_and_usage():
    client, completions = _client(_response(_text_plan()))

    result, input_tokens, output_tokens = revise_landing_page_hybrid(
        source_html="<html><head><title>Shop</title></head><body><h1 id='title'>Old</h1></body></html>",
        revision_prompt="Change Old to New",
        client=client,
        applier=lambda source, plan, chromium_path=None: {"html": source.replace("Old", "New")},
    )

    assert result.metadata["final_route"] == "PATCH"
    assert result.metadata["patch_attempts"] == 1
    assert result.title == "Shop"
    assert "New" in result.html
    assert (input_tokens, output_tokens) == (100, 20)
    assert len(completions.calls) == 1


def test_selector_failure_retries_once_with_feedback():
    client, completions = _client(_response(_text_plan("#missing")), _response(_text_plan()))
    calls = 0

    def applier(source, plan, chromium_path=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PatchError("target_count_mismatch", "#missing found=0 expected=1")
        return {"html": source.replace("Old", "New")}

    result, input_tokens, output_tokens = revise_landing_page_hybrid(
        source_html="<html><title>Shop</title><h1 id='title'>Old</h1></html>",
        revision_prompt="Change Old to New",
        client=client,
        applier=applier,
    )

    assert result.metadata["final_route"] == "PATCH"
    assert result.metadata["patch_attempts"] == 2
    assert (input_tokens, output_tokens) == (200, 40)
    assert len(completions.calls[1]["messages"]) == 4
    assert "target_count_mismatch" in completions.calls[1]["messages"][-1]["content"]


def test_thirty_patches_go_directly_to_full_fallback():
    large_plan = {"version": "1", "patches": [_text_plan()["patches"][0]] * 30}
    client, completions = _client(_response(large_plan))
    full = LandingPageGenerationResponse(
        title="Full",
        html=("<html><head><style>body{color:black}</style></head><body>full"
              "<script>document.body.dataset.ready='1'</script></body></html>"),
    )

    result, input_tokens, output_tokens = revise_landing_page_hybrid(
        source_html="<html><title>Old</title><h1 id='title'>Old</h1></html>",
        revision_prompt="Redesign everything",
        client=client,
        applier=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not apply")),
        full_generator=lambda _source, _request: (full, 300, 400),
    )

    assert result.metadata["final_route"] == "FULL"
    assert result.metadata["fallback_reason"] == "patch_count_threshold"
    assert result.metadata["patch_attempts"] == 1
    assert result.metadata["full_attempts"] == 1
    assert (input_tokens, output_tokens) == (400, 420)
    assert len(completions.calls) == 1
