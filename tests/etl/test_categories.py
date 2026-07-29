# tests/etl/test_categories.py
import json

import pandas as pd
import pytest

from etl.categories import CategoryStore
from etl.config import DATA_DIR


@pytest.fixture()
def store(tmp_path):
    path = tmp_path / "cats.json"
    path.write_text(json.dumps({"nytimes.com": "News"}), encoding="utf-8")
    return CategoryStore(path)


def test_known_domain_resolves(store):
    assert store.category_of("nytimes.com") == "News"


def test_unknown_domain_is_excluded_not_guessed(store):
    assert store.category_of("i.redd.it") is None
    assert not store.is_allowed("i.redd.it")


def test_unknown_domains_are_recorded_for_later(store):
    store.category_of("newsite.example")
    store.category_of("newsite.example")
    assert store.pending == {"newsite.example"}


def test_learn_persists_to_disk(store):
    store.learn({"newsite.example": "Blogs"})
    store.save()
    reloaded = CategoryStore(store.path)
    assert reloaded.category_of("newsite.example") == "Blogs"


def test_seed_cache_covers_the_committed_domains():
    seed = json.loads((DATA_DIR / "domain_categories.json").read_text(encoding="utf-8"))
    ctdd = pd.read_csv(DATA_DIR / "clean_top_distinctive_domains.csv")
    assert set(ctdd.domain) <= set(seed)
