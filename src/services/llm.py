"""AgentCore Platform v1.0 — runtime LLM adapter for EDU-C2-095 (reviewer summary only).

PostProcessNode is injected with this adapter at graph construction (tests inject a fake). Only the
optional reviewer-summary path calls it; the deterministic validator core never does, and the summary
is best-effort — an unavailable / failing adapter degrades to the deterministic completeness packet,
never an error. The adapter resolves the Azure connection values at CALL time from the request-bound
SecretProvider (``InvocationContext.from_state(state).secrets.require`` — never ``os.environ``, never
State, never a build-time / import-time read), builds a shared client, and returns the summary text.
"""

from typing import Any

from framework.schemas.invocation_context import InvocationContext


# Connection values the deployment binds for the Azure OpenAI endpoint. Declared in
# config/agent.yaml requires.secrets and resolved from the request-bound provider at complete()
# time. AZURE_OPENAI_ENDPOINT must be the bare resource endpoint: the client rejects a value that
# already carries an API path. AZURE_OPENAI_DEPLOYMENT is the deployment name, which the endpoint
# uses as its model routing key.
_API_KEY = "AZURE_OPENAI_API_KEY"
_ENDPOINT = "AZURE_OPENAI_ENDPOINT"
_DEPLOYMENT = "AZURE_OPENAI_DEPLOYMENT"


def _build_azure_client(config: dict[str, Any]) -> Any:
    """Construct the shared LLM client, lazily imported so the provider extra is only needed on the
    reviewer-summary path and so tests can substitute the whole adapter."""
    from shared.services.llm.azure_openai_client import AzureOpenAIClient

    return AzureOpenAIClient(config)


def _extract_text(response: Any) -> str:
    """Extract the completion text from the shared client's response (tolerant of shapes)."""
    if isinstance(response, str):
        return response.strip()
    if isinstance(response, dict):
        return str(response.get("content", "")).strip()
    content = getattr(response, "content", None)
    if content is not None:
        return str(content).strip()
    return str(response).strip()


class ReviewerSummaryLLM:
    """Runtime adapter: resolve the connection values per request, call the shared client with a messages list,
    return the summary text. Interface used by PostProcessNode: ``complete(messages, state) -> str``.
    Best-effort at the node: a raised error here (including a missing secret) is caught and degraded
    to the deterministic packet — the key is never placed in State / trace / error."""

    def __init__(self, temperature: float = 0.0) -> None:
        self._temperature = temperature

    def complete(self, messages: list[dict[str, str]], state: dict[str, Any]) -> str:
        # Resolve the key at call time from the request-bound provider — NOT at startup.
        ctx = InvocationContext.from_state(state)
        secrets = ctx.secrets
        client = _build_azure_client(
            {
                "api_key": secrets.require(_API_KEY),
                "azure_endpoint": secrets.require(_ENDPOINT),
                "azure_deployment": secrets.require(_DEPLOYMENT),
                "temperature": self._temperature,
            }
        )
        return _extract_text(client.complete(messages))
