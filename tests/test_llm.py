import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import patch, MagicMock
from lib.llm import call_model, call_models_parallel, LLMError


def make_ok_response(content):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"choices": [{"message": {"content": content}}]}
    return m


def make_err_response(status, body):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = body
    m.text = str(body)
    return m


def test_call_model_returns_content():
    with patch("lib.llm.requests.post", return_value=make_ok_response("hello")) as mock:
        result = call_model("Azure/gpt-4o", [{"role": "user", "content": "hi"}])
        assert result == "hello"
        assert mock.called


def test_call_model_raises_on_http_error():
    with patch("lib.llm.requests.post", return_value=make_err_response(401, {"error": "unauthorized"})):
        try:
            call_model("Azure/gpt-4o", [{"role": "user", "content": "hi"}])
            assert False, "should raise"
        except LLMError as e:
            assert "401" in str(e)


def test_call_models_parallel_returns_all():
    with patch("lib.llm.requests.post", return_value=make_ok_response("response")):
        results = call_models_parallel(
            ["Azure/gpt-4o", "GCP/gemini-2.5-flash", "aws/claude-opus-4-6"],
            [{"role": "user", "content": "hi"}],
        )
        assert len(results) == 3
        assert all(r == "response" for r in results.values())


def test_call_models_parallel_captures_partial_failure():
    ok_resp = make_ok_response("ok")
    err_resp = make_err_response(500, {"error": "internal"})
    err_resp.text = "internal error"
    ok2_resp = make_ok_response("ok2")

    model_responses = {
        "Azure/gpt-4o": ok_resp,
        "GCP/gemini-2.5-flash": err_resp,
        "aws/claude-opus-4-6": ok2_resp,
    }

    def side_effect(*args, **kwargs):
        model = kwargs.get("json", {}).get("model", "")
        return model_responses.get(model, make_ok_response("fallback"))

    with patch("lib.llm.requests.post", side_effect=side_effect):
        results = call_models_parallel(
            ["Azure/gpt-4o", "GCP/gemini-2.5-flash", "aws/claude-opus-4-6"],
            [{"role": "user", "content": "hi"}],
        )
        assert results["Azure/gpt-4o"] == "ok"
        assert isinstance(results["GCP/gemini-2.5-flash"], LLMError)
        assert results["aws/claude-opus-4-6"] == "ok2"
