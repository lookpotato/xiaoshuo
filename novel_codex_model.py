"""Pinned Codex model for every stage of the novel production workflow."""

MODEL = "gpt-6-sol"
REASONING_EFFORT = "medium"


def exec_prefix(codex: str) -> list[str]:
    return [
        codex, "exec", "--model", MODEL,
        "--config", f'model_reasoning_effort="{REASONING_EFFORT}"',
    ]
