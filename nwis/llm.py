"""Single LLM/embedding client. Fully local via Ollama.

Usage:
    from nwis.llm import complete, extract, embed, health
    obj = extract(SomePydanticModel, system, user)      # structured, validated, retried
    txt = complete(system, user)                         # free text
    vecs = embed(["..."])                                # list[list[float]]
"""
from __future__ import annotations

from typing import Type, TypeVar

import httpx
from pydantic import BaseModel

from nwis.config import settings

T = TypeVar("T", bound=BaseModel)


def _openai_client():
    from openai import OpenAI
    return OpenAI(base_url=settings.ollama_base_url, api_key="ollama")


def _instructor_client():
    import instructor
    return instructor.from_openai(_openai_client(), mode=instructor.Mode.JSON), settings.ollama_model


def _no_think(system: str) -> str:
    """Qwen3 thinks by default (10x slower, no quality gain on extraction).
    Force it off via the soft switch + Ollama's native flag when a qwen3 model is selected."""
    if settings.ollama_model.startswith("qwen3"):
        return system.rstrip() + "\n/no_think"
    return system


def _ollama_extra() -> dict:
    return {"extra_body": {"think": False}} if settings.ollama_model.startswith("qwen3") else {}


def extract(model: Type[T], system: str, user: str, max_retries: int = 2, temperature: float = 0.0) -> T:
    client, name = _instructor_client()
    kwargs = dict(
        model=name,
        response_model=model,
        max_retries=max_retries,
        messages=[{"role": "system", "content": _no_think(system)}, {"role": "user", "content": user}],
        temperature=temperature,
    )
    kwargs.update(_ollama_extra())
    return client.chat.completions.create(**kwargs)


def complete(system: str, user: str, temperature: float = 0.2, max_tokens: int = 800) -> str:
    r = _openai_client().chat.completions.create(
        model=settings.ollama_model, temperature=temperature, max_tokens=max_tokens,
        messages=[{"role": "system", "content": _no_think(system)}, {"role": "user", "content": user}],
        **_ollama_extra(),
    )
    return r.choices[0].message.content or ""


def embed(texts: list[str]) -> list[list[float]]:
    """Always local (nomic-embed-text via Ollama)."""
    base = settings.ollama_base_url.replace("/v1", "")
    if not texts:
        return []
    with httpx.Client(timeout=300) as c:
        # Batched /api/embed (Ollama >= 0.3): one request per batch, model stays resident.
        # keep_alive keeps the embedder loaded next to the chat model instead of swapping.
        try:
            out: list[list[float]] = []
            for i in range(0, len(texts), 64):
                r = c.post(f"{base}/api/embed", json={
                    "model": settings.ollama_embed_model, "input": texts[i:i + 64], "keep_alive": "30m",
                })
                r.raise_for_status()
                vecs = r.json()["embeddings"]
                if len(vecs) != len(texts[i:i + 64]):
                    raise KeyError("embedding count mismatch")  # -> legacy per-text path below
                out.extend(vecs)
            return out
        except (httpx.HTTPStatusError, KeyError):
            # Older Ollama: fall back to the one-at-a-time legacy endpoint.
            out = []
            for t in texts:
                r = c.post(f"{base}/api/embeddings", json={"model": settings.ollama_embed_model, "prompt": t})
                r.raise_for_status()
                out.append(r.json()["embedding"])
            return out


def health() -> dict:
    base = settings.ollama_base_url.replace("/v1", "")
    try:
        with httpx.Client(timeout=5) as c:
            tags = c.get(f"{base}/api/tags").json()
        names = [m["name"] for m in tags.get("models", [])]
        return {
            "ollama": True,
            "chat_model_present": settings.ollama_model in names,
            "embed_model_present": any(n.startswith(settings.ollama_embed_model) for n in names),
        }
    except Exception as e:  # noqa: BLE001
        return {"ollama": False, "error": str(e)}
