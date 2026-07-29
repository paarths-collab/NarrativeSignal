# tests/etl/test_no_dead_imports.py
"""Guards for Task 18 (dead-code deletion in backend/).

Three backend paths were removed because nothing live imported them:

  * ``backend/database.py``  -- orphaned. It built ``CREATE OR REPLACE VIEW
    narratives AS SELECT narrative_id AS internal_system_id, title AS
    narrative_theme, domains, top_subreddits, top_authors FROM
    read_csv_auto(...)``, but ``narrative_intelligence_summary.csv`` has none
    of ``title``/``domains``/``top_subreddits``/``top_authors``, so every call
    raised and callers swallowed the exception. Worse, it opened the DB
    read-write and created *views* over names ``patch_db.py`` creates as
    *tables*; DuckDB views persist across connections, so it could shadow the
    ``narratives`` table and break every networkgraph join.
  * ``backend/hybrid/``      -- imported only by ``backend/hybrid_crew/``.
  * ``backend/hybrid_crew/`` -- referenced in ``main.py`` only from a docstring
    and a commented-out block.

``backend/hybrid_chatbot/`` is the LIVE chatbot package (``main.py`` mounts
``hybrid_chatbot.api``). The names are confusingly similar, so this module also
asserts it survived.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"


def test_dead_backend_database_module_is_deleted():
    assert not (BACKEND / "database.py").exists(), (
        "backend/database.py should be deleted: it is unreferenced and its "
        "views collide with the tables patch_db.py creates"
    )


def test_dead_hybrid_package_is_deleted():
    assert not (BACKEND / "hybrid").exists(), (
        "backend/hybrid/ should be deleted: only backend/hybrid_crew/ imported it"
    )


def test_dead_hybrid_crew_package_is_deleted():
    assert not (BACKEND / "hybrid_crew").exists(), (
        "backend/hybrid_crew/ should be deleted: main.py references it only in "
        "a docstring and a commented-out block"
    )


def test_live_hybrid_chatbot_package_survives():
    """Tripwire against deleting the wrong lookalike package.

    main.py mounts ``hybrid_chatbot.api``; tests/etl/test_pipeline.py imports
    ``hybrid_chatbot.config.CSV_TABLES``.
    """
    config = BACKEND / "hybrid_chatbot" / "config.py"
    assert config.exists(), (
        "backend/hybrid_chatbot/config.py is LIVE and must not be deleted"
    )
    assert "CSV_TABLES" in config.read_text(encoding="utf-8")


def test_main_py_does_not_open_with_a_large_commented_out_block():
    """main.py carried ~453 lines of a commented-out earlier copy of the app.

    Assert that real code appears near the top rather than after hundreds of
    lines of dead comments.
    """
    lines = (BACKEND / "main.py").read_text(encoding="utf-8").splitlines()
    non_blank = [ln for ln in lines if ln.strip()]
    head = non_blank[:50]
    real_code = [ln for ln in head if not ln.lstrip().startswith("#")]
    assert real_code, (
        "the first 50 non-blank lines of backend/main.py are all comments -- "
        "the commented-out legacy app block is still present"
    )


def test_narrative_id_float_bridge_is_retained():
    """DELIBERATELY RETAINED -- do not "clean this up".

    ``data/hybrid_vector_meta.jsonl`` still stores old-format float narrative
    ids (e.g. ``0.0_salon.com``) while the regenerated registry uses
    ``0_salon.com``. ``_norm_nid`` in networkgraph is the bridge that lets those
    ids resolve against the registry. Removing it silently breaks retrieval /
    the networkgraph joins. It stays until the vector index is rebuilt, which is
    a separate future task -- this test exists so a later cleanup pass cannot
    delete it without a failing test explaining why.
    """
    utils = BACKEND / "networkgraph" / "routers" / "utils.py"
    src = utils.read_text(encoding="utf-8")
    assert 'replace(".0_", "_")' in src, (
        "the .0_ -> _ narrative-id bridge in "
        "backend/networkgraph/routers/utils.py::_norm_nid was removed; "
        "data/hybrid_vector_meta.jsonl still contains float-format ids and "
        "depends on it"
    )

    # And the stale ids it bridges really are still on disk.
    meta = REPO_ROOT / "data" / "hybrid_vector_meta.jsonl"
    if meta.exists():
        with meta.open(encoding="utf-8") as fh:
            assert any(".0_" in line for line in fh), (
                "hybrid_vector_meta.jsonl no longer has float-format ids; the "
                "bridge may now be removable -- re-verify before deleting"
            )
