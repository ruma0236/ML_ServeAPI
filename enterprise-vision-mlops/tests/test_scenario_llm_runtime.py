from __future__ import annotations

from evm.model_runtime.llm import instruction_content, instruction_messages, token_f1


def record() -> dict:
    return {
        "instruction": "Name the capital of France.",
        "context": "France is a country in Europe.",
        "response": "Paris",
    }


def test_instruction_contract_preserves_context_and_answer_boundary() -> None:
    assert "Context:" in instruction_content(record())
    messages = instruction_messages(record(), include_response=True)
    assert messages[0]["role"] == "user"
    assert messages[-1] == {"role": "assistant", "content": "Paris"}


def test_token_f1_is_bounded_and_order_independent() -> None:
    assert token_f1("Paris", "Paris") == 1.0
    assert token_f1("Paris France", "France Paris") == 1.0
    assert token_f1("unknown", "Paris") == 0.0
