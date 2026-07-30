"""Add Google AI embedding support missing from Letta 0.16.8.

Letta advertises ``google_ai`` as an embedding endpoint type, but its pinned
GoogleAIClient inherits the abstract ``request_embeddings`` implementation.
Archival passage insertion therefore fails before contacting Google.  Keep this
patch narrow and refuse to apply it if the pinned upstream file changes.
"""

from pathlib import Path


TARGET = Path("/app/letta/llm_api/google_ai_client.py")

IMPORT_OLD = "from google.genai.types import HttpOptions\n"
IMPORT_NEW = "from google.genai.types import EmbedContentConfig, HttpOptions\n"

SCHEMA_IMPORT_OLD = "from letta.schemas.llm_config import LLMConfig\n"
SCHEMA_IMPORT_NEW = (
    "from letta.schemas.embedding_config import EmbeddingConfig\n"
    "from letta.schemas.llm_config import LLMConfig\n"
)

METHOD_ANCHOR = """
    async def _get_client_async(self, llm_config: Optional[LLMConfig] = None):
        timeout_ms = int(model_settings.gemini_timeout_seconds * 1000)
        api_key = None
        if llm_config:
            api_key, _, _ = await self.get_byok_overrides_async(llm_config)
        if not api_key:
            api_key = model_settings.gemini_api_key
        return genai.Client(
            api_key=api_key,
            http_options=HttpOptions(timeout=timeout_ms),
        )
"""

METHOD_REPLACEMENT = METHOD_ANCHOR + """

    async def request_embeddings(
        self,
        texts: List[str],
        embedding_config: EmbeddingConfig,
    ) -> List[List[float]]:
        if not texts:
            return []

        client = await self._get_client_async()
        response = await client.aio.models.embed_content(
            model=embedding_config.embedding_model,
            contents=texts,
            config=EmbedContentConfig(
                output_dimensionality=embedding_config.embedding_dim,
            ),
        )
        embeddings = response.embeddings or []
        values = [list(embedding.values or []) for embedding in embeddings]

        if len(values) != len(texts):
            raise RuntimeError(
                "Google AI returned an unexpected number of embeddings: "
                f"expected {len(texts)}, got {len(values)}"
            )
        for index, vector in enumerate(values):
            if len(vector) != embedding_config.embedding_dim:
                raise RuntimeError(
                    "Google AI returned an unexpected embedding dimension at "
                    f"index {index}: expected {embedding_config.embedding_dim}, "
                    f"got {len(vector)}"
                )
        return values
"""


def replace_once(source: str, old: str, new: str, label: str) -> str:
    found = source.count(old)
    if found != 1:
        raise RuntimeError(
            f"refusing compatibility patch: expected one {label} in {TARGET}, "
            f"found {found}"
        )
    return source.replace(old, new, 1)


def main() -> None:
    source = TARGET.read_text(encoding="utf-8")
    source = replace_once(source, IMPORT_OLD, IMPORT_NEW, "Google types import")
    source = replace_once(
        source,
        SCHEMA_IMPORT_OLD,
        SCHEMA_IMPORT_NEW,
        "EmbeddingConfig import anchor",
    )
    source = replace_once(
        source,
        METHOD_ANCHOR,
        METHOD_REPLACEMENT,
        "GoogleAIClient async client method",
    )
    TARGET.write_text(source, encoding="utf-8")

    patched = TARGET.read_text(encoding="utf-8")
    required = (
        "EmbedContentConfig",
        "EmbeddingConfig",
        "async def request_embeddings(",
        "output_dimensionality=embedding_config.embedding_dim",
    )
    missing = [value for value in required if value not in patched]
    if missing:
        raise RuntimeError(f"post-patch verification failed; missing {missing}")


if __name__ == "__main__":
    main()
