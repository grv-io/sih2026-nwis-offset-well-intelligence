"""Zero-dependency demo mode (IMPLEMENTATION_PLAN §6 A5): a JSON cache of
Q -> Answer at `models/demo_qa_cache.json`, consulted first so the 6 rehearsed
demo questions keep working even if Ollama is down on stage (OffsetEye
`CACHED_DEMO_KEY` pattern).

CLI:
    python -m nwis.search.demo_cache --warm [questions.txt]
        Computes (live, use_cache=False) and caches an answer for every
        question in questions.txt (one per line, '#' comments/blank lines
        skipped), or for the 6 built-in default questions if no file is given.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from nwis.config import settings
from nwis.search import answer as answer_mod
from nwis.search.answer import Answer, Citation

CACHE_PATH: Path = settings.models_dir / "demo_qa_cache.json"

DEFAULT_QUESTIONS: list[str] = [
    "Girujan stuck pipe remedy",
    "Tipam losses LCM",
    "Barail kick mud weight",
    "cementing problems at 9-5/8 casing",
    "worst NPT well in Moran",
    "what happened at 2,400 m near DUL-005",
]


def _key(query: str, filters: Optional[dict] = None) -> str:
    norm = query.strip().lower()
    if filters:
        norm += "|" + json.dumps(filters, sort_keys=True)
    return norm


def _load_raw() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_raw(data: dict) -> None:
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _answer_to_dict(a: Answer) -> dict:
    return asdict(a)


def _dict_to_answer(d: dict) -> Answer:
    citations = [Citation(**c) for c in d.get("citations", [])]
    return Answer(
        text=d.get("text", ""),
        citations=citations,
        structured=d.get("structured", []),
        refused=d.get("refused", False),
        degraded=d.get("degraded", False),
    )


def get_cached(query: str, filters: Optional[dict] = None) -> Optional[Answer]:
    raw = _load_raw()
    entry = raw.get(_key(query, filters))
    return _dict_to_answer(entry) if entry is not None else None


def set_cached(query: str, ans: Answer, filters: Optional[dict] = None) -> None:
    raw = _load_raw()
    raw[_key(query, filters)] = _answer_to_dict(ans)
    _save_raw(raw)


def answer(query: str, filters: Optional[dict] = None, use_cache: bool = True) -> Answer:
    """Drop-in replacement for `nwis.search.answer.answer` that consults the
    demo cache first (offline/API-down resilience). On a cache miss it computes
    live; if that live call blows up (e.g. Ollama unreachable), it degrades
    gracefully to a refusal instead of crashing the demo."""
    if use_cache:
        cached = get_cached(query, filters)
        if cached is not None:
            return cached

    try:
        result = answer_mod.answer(query, filters)
    except Exception as e:  # noqa: BLE001 -- demo must never hard-crash
        return Answer(
            text=f"Live answer unavailable ({e.__class__.__name__}) and no cached answer "
                 f"for this question.",
            citations=[], structured=[], refused=True, degraded=True,
        )
    return result


def warm(questions: list[str]) -> None:
    print(f"Warming demo cache with {len(questions)} question(s) -> {CACHE_PATH}")
    for i, q in enumerate(questions, start=1):
        t0 = time.time()
        try:
            a = answer_mod.answer(q, filters=None)
            set_cached(q, a)
            dt = time.time() - t0
            print(f"[{i}/{len(questions)}] {q!r}: cached (refused={a.refused}, "
                  f"degraded={a.degraded}, {dt:.2f}s)")
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(questions)}] {q!r}: FAILED ({e})")


def _read_questions_file(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Warm the demo Q->Answer cache.")
    parser.add_argument("--warm", nargs="?", const="__default__", default=None,
                         metavar="questions.txt",
                         help="Warm the cache; optionally from a questions file (one per line)")
    args = parser.parse_args(argv)

    if args.warm is not None:
        if args.warm != "__default__":
            path = Path(args.warm)
            questions = _read_questions_file(path) if path.exists() else DEFAULT_QUESTIONS
        else:
            questions = DEFAULT_QUESTIONS
        warm(questions)
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
