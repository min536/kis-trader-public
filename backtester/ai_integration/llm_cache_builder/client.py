"""LLM client abstractions for the offline batch cache generator.

The public interface is the ``LLMClient`` Protocol — any object with a
``complete(system, messages) -> str`` method.

Concrete implementations
------------------------
``AnthropicClient``  — wraps the ``anthropic`` SDK (pip install anthropic).
``MockLLMClient``    — deterministic mock for unit tests; returns
                       caller-supplied responses in sequence.

Model routing
-------------
Use the constants below to choose a model tier:

  ``SMOKE_TEST_MODEL``  — claude-haiku-3-5   cheapest; start here
  ``PILOT_MODEL``       — claude-sonnet-4-5  mid-range; expand after smoke test
  ``PRODUCTION_MODEL``  — claude-opus-4-5    most capable; for final runs

``DEFAULT_MODEL`` is set to ``SMOKE_TEST_MODEL`` so a first run is cheap by
default.  Pass ``--model`` on the CLI to override.
"""
from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable


# ── Model registry ────────────────────────────────────────────────────────

#: Cheapest option — recommended starting point for smoke tests.
SMOKE_TEST_MODEL = "claude-haiku-4-5"
#: Mid-range option — expand to this after smoke test looks clean.
PILOT_MODEL = "claude-sonnet-4-6"
#: Most capable option — use for final production-quality cache runs.
PRODUCTION_MODEL = "claude-opus-4-6"

#: All known models with a one-line description of their intended use tier.
KNOWN_MODELS: dict[str, str] = {
    SMOKE_TEST_MODEL: "cheapest  — recommended for first smoke tests",
    PILOT_MODEL:      "mid-range — expand after smoke test looks clean",
    PRODUCTION_MODEL: "most capable — use for production cache generation",
}

#: The model used when ``--model`` is not specified.
#: Deliberately set to the cheapest option so accidental full runs don't
#: burn budget unexpectedly.
DEFAULT_MODEL: str = SMOKE_TEST_MODEL


# ── Protocol ──────────────────────────────────────────────────────────────


@runtime_checkable
class LLMClient(Protocol):
    """Minimal interface every LLM client must satisfy."""

    def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
    ) -> str:
        """Submit a conversation and return the assistant's text response.

        Parameters
        ----------
        system:
            System-prompt string (plain text, not a dict).
        messages:
            OpenAI-style list of ``{"role": "user"|"assistant", "content": "…"}``
            dicts, starting with the first user turn.
        """
        ...


# ── Anthropic ─────────────────────────────────────────────────────────────


def model_note(model: str) -> str:
    """Return a one-line startup note describing the active model choice.

    Shown at the start of a generation run so the user can see at a glance
    whether they are using the smoke-test default or an explicit override.

    Examples
    --------
    >>> model_note(SMOKE_TEST_MODEL)
    'Smoke-test default model : claude-haiku-3-5  (cheapest  — recommended for first smoke tests)'
    >>> model_note("claude-sonnet-4-5")
    'Using override model     : claude-sonnet-4-5  (mid-range — expand after smoke test looks clean)'
    """
    description = KNOWN_MODELS.get(model, "custom / unknown model")
    if model == DEFAULT_MODEL:
        label = "Smoke-test default model"
    else:
        label = "Using override model    "
    return f"{label} : {model}  ({description})"


class AnthropicClient:
    """Thin wrapper around the Anthropic Messages API.

    Requires the ``anthropic`` package (``pip install anthropic``).
    The API key is read from the *ANTHROPIC_API_KEY* environment variable
    by default; pass ``api_key=`` to override.
    """

    DEFAULT_MODEL = DEFAULT_MODEL  # re-exported for backward compatibility

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 4096,
    ) -> None:
        try:
            import anthropic as _anthropic  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required. "
                "Install it with:  pip install anthropic"
            ) from exc
        self._client = _anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
        )
        self._model = model
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        return self._model

    def complete(self, system: str, messages: list[dict[str, str]]) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            output_config={"effort": "low"},
            system=system,
            messages=messages,  # type: ignore[arg-type]
        )
        return response.content[0].text  # type: ignore[index]


# ── Mock (tests / dry-run) ────────────────────────────────────────────────


class MockLLMClient:
    """Deterministic mock that returns pre-configured responses in order.

    Parameters
    ----------
    responses:
        Sequence of strings to return, one per ``complete()`` call.
        Raises ``RuntimeError`` if called more times than there are
        configured responses.

    Usage::

        client = MockLLMClient([valid_json_day1, valid_json_day2])
        gen = BatchCacheGenerator(client, ...)
        gen.generate(features_path)
        assert len(client.calls) == 2
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._index = 0
        #: Every recorded call as ``{"system": …, "messages": […]}``.
        self.calls: list[dict[str, Any]] = []

    def complete(self, system: str, messages: list[dict[str, str]]) -> str:
        self.calls.append({"system": system, "messages": list(messages)})
        if self._index >= len(self._responses):
            raise RuntimeError(
                f"MockLLMClient exhausted: {self._index} calls made but only "
                f"{len(self._responses)} response(s) configured."
            )
        result = self._responses[self._index]
        self._index += 1
        return result
