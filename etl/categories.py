"""Domain allowlist and category cache (spec §10.1).

The original hand-curated allowlist is lost. This replaces it with a growing cache:
a domain is *in the media universe* iff it has a category. Unknown domains are
recorded in `pending` so a later LLM pass can categorize them once, forever.
"""
import json
from pathlib import Path


class CategoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._cats: dict[str, str] = (
            json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        )
        self.pending: set[str] = set()

    def category_of(self, domain: str) -> str | None:
        """Category, or None for a domain outside the media universe."""
        if domain in self._cats:
            return self._cats[domain]
        self.pending.add(domain)
        return None

    def is_allowed(self, domain: str) -> bool:
        return self.category_of(domain) is not None

    @property
    def mapping(self) -> dict[str, str]:
        """Read-only view of the allowlist, for transforms that need the whole dict."""
        return dict(self._cats)

    def learn(self, mapping: dict[str, str]) -> None:
        self._cats.update(mapping)
        self.pending -= set(mapping)

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self._cats, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def __len__(self) -> int:
        return len(self._cats)
