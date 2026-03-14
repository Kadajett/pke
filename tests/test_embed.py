"""Tests for the embedding module (mock Ollama responses)."""

from unittest.mock import MagicMock, patch

from pke.embed import embed_batch, embed_text


@patch("pke.embed.ollama_client.Client")
def test_embed_text(mock_client_cls):
    mock_client = MagicMock()
    mock_client.embed.return_value = {"embeddings": [[0.1] * 768]}
    mock_client_cls.return_value = mock_client

    result = embed_text("hello world")
    assert len(result) == 768
    assert result[0] == 0.1
    mock_client.embed.assert_called_once()


@patch("pke.embed.ollama_client.Client")
def test_embed_batch(mock_client_cls):
    mock_client = MagicMock()
    mock_client.embed.return_value = {"embeddings": [[0.1] * 768, [0.2] * 768]}
    mock_client_cls.return_value = mock_client

    result = embed_batch(["hello", "world"])
    assert len(result) == 2
    assert len(result[0]) == 768


@patch("pke.embed.ollama_client.Client")
def test_embed_batch_empty(mock_client_cls):
    result = embed_batch([])
    assert result == []
    mock_client_cls.assert_not_called()
