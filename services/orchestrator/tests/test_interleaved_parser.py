from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.agent_runtime import _extract_interleaved_parts


def test_extract_interleaved_parts_with_text_and_image() -> None:
    response = SimpleNamespace(
        response_id="resp_123",
        candidates=[
            SimpleNamespace(
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(text="A tense chamber fills with silence."),
                        SimpleNamespace(
                            inline_data=SimpleNamespace(
                                mime_type="image/png",
                                data=b"\x89PNG\r\n",
                            )
                        ),
                    ]
                )
            )
        ],
    )

    request_id, blocks = _extract_interleaved_parts(response)

    assert request_id == "resp_123"
    assert len(blocks) == 2
    assert blocks[0].kind == "text"
    assert blocks[0].part_order == 0
    assert blocks[0].text == "A tense chamber fills with silence."
    assert blocks[1].kind == "image"
    assert blocks[1].part_order == 1
    assert blocks[1].mime_type == "image/png"
    assert blocks[1].inline_data_b64


def test_extract_interleaved_parts_requires_text_and_image() -> None:
    response = SimpleNamespace(
        response_id="resp_124",
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="Only narration")]))],
    )

    with pytest.raises(RuntimeError, match="must include both text and image"):
        _extract_interleaved_parts(response)
