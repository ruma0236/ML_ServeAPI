from __future__ import annotations

from evm.model_runtime.common import parse_choice_index
from evm.model_runtime.vlm import scienceqa_messages, scienceqa_prompt


def record() -> dict:
    return {
        "sample_id": "scienceqa-1",
        "question": "Which object is red?",
        "choices": ["ball", "tree", "sky"],
        "answer_index": 0,
    }


def test_scienceqa_prompt_uses_explicit_zero_based_options() -> None:
    prompt = scienceqa_prompt(record())
    assert "0. ball" in prompt
    assert "2. sky" in prompt
    assert "only the option number" in prompt


def test_training_messages_add_exact_assistant_answer() -> None:
    messages = scienceqa_messages(record(), include_answer=True)
    assert messages[-1] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "0"}],
    }


def test_choice_parser_rejects_out_of_range_numbers() -> None:
    assert parse_choice_index("Answer: 2", 3) == 2
    assert parse_choice_index("There are 20 items. Answer: 1", 3) == 1
    assert parse_choice_index("Answer: 9", 3) is None
    assert parse_choice_index("unknown", 3) is None
