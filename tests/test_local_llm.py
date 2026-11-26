from unittest.mock import patch

from app.services.local_llm import LocalLLM


@patch("app.services.local_llm.ollama.chat")
def test_local_llm_response(mock_chat):
    mock_chat.return_value = {
        "message": {
            "content": "PostgreSQL is a relational database."
        }
    }

    llm = LocalLLM()

    response = llm.generate_response(
        "Explain in one sentence what PostgreSQL is."
    )

    assert response == "PostgreSQL is a relational database."

    mock_chat.assert_called_once()
    kwargs = mock_chat.call_args.kwargs
    assert kwargs["format"]["type"] == "object"
    assert kwargs["options"]["temperature"] == 0
