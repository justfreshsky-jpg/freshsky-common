from tools.scan_models import _is_zero_cost_openrouter_pricing, _propose_for


def test_openrouter_free_pricing_requires_zero_input_and_output():
    assert _is_zero_cost_openrouter_pricing(
        {"prompt": "0", "completion": "0", "request": "0"}
    )
    assert not _is_zero_cost_openrouter_pricing(
        {"prompt": "0", "completion": "0.000001"}
    )
    assert not _is_zero_cost_openrouter_pricing({"prompt": "0"})
    assert not _is_zero_cost_openrouter_pricing(
        {"prompt": "0", "completion": "0", "request": "unknown"}
    )


def test_model_proposal_requires_strictly_higher_rank():
    info = {"current": "llama-3.3-70b-versatile", "prefer_size": "70b"}

    assert _propose_for("groq", info, ["llama-3.1-8b"]) is None
    assert (
        _propose_for("groq", info, ["llama-4-scout-17b"])
        == "llama-4-scout-17b"
    )
