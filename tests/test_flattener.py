"""
Tests for JsonFlattener, SchemaResolver, FlatRecord, and FlatDuckDBSink.

The central claim mirrors the clinical-trial adapter tests:
two feeds with the same semantic data in different nesting structures should
produce the same canonical FlatRecord after flattening + resolving.

This is the sports version of test_both_adapters_produce_equal_trial_records.
"""

import json
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb", reason="duckdb not installed")

from ingestion.flattener import (
    FlatRecord,
    JsonFlattener,
    SchemaResolver,
    flatten_to_record,
)
from ingestion.sinks.flat_sink import FlatDuckDBSink

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def game_v1():
    data = json.loads((FIXTURES / "sports_game.json").read_text())
    data.pop("_comment", None)
    return data


@pytest.fixture
def game_v2():
    data = json.loads((FIXTURES / "sports_game_v2.json").read_text())
    data.pop("_comment", None)
    return data


@pytest.fixture
def resolver():
    from scripts.run_sports import SPORTS_RESOLVER
    return SPORTS_RESOLVER


# ──────────────────────────────────────────────────────────────────────────────
# JsonFlattener — basic behaviour
# ──────────────────────────────────────────────────────────────────────────────


def test_flat_scalar_passthrough():
    result = JsonFlattener().flatten({"game_id": "G001", "sport": "basketball"})
    assert result == {"game_id": "G001", "sport": "basketball"}


def test_one_level_nesting():
    result = JsonFlattener().flatten({"home": {"score": 108, "team": "Lakers"}})
    assert result["home_score"] == 108
    assert result["home_team"] == "Lakers"


def test_two_side_collision_resolved():
    """home.score and away.score must produce distinct keys."""
    result = JsonFlattener().flatten({
        "home": {"score": 108},
        "away": {"score": 103},
    })
    assert result["home_score"] == 108
    assert result["away_score"] == 103
    assert len(result) == 2


def test_deep_nesting_period_scores():
    """periods.1.home.score must become periods_1_home_score."""
    result = JsonFlattener().flatten({
        "periods": {"1": {"home": {"score": 28}, "away": {"score": 25}}}
    })
    assert result["periods_1_home_score"] == 28
    assert result["periods_1_away_score"] == 25


def test_score_key_appears_many_times_all_unique():
    """
    'score' appears 10 times in a 4-period game (2 totals + 4×2 period scores).
    Every flattened key must be unique.
    """
    flat = JsonFlattener().flatten({
        "home": {"score": 108},
        "away": {"score": 103},
        "periods": {
            "1": {"home": {"score": 28}, "away": {"score": 25}},
            "2": {"home": {"score": 26}, "away": {"score": 27}},
            "3": {"home": {"score": 30}, "away": {"score": 28}},
            "4": {"home": {"score": 24}, "away": {"score": 23}},
        },
    })
    score_keys = [k for k in flat if "score" in k]
    assert len(score_keys) == 10
    assert len(set(score_keys)) == 10  # all unique


def test_list_items_indexed():
    result = JsonFlattener().flatten({"tags": ["a", "b", "c"]})
    assert result["tags_0"] == "a"
    assert result["tags_2"] == "c"


def test_comment_key_skipped_by_default():
    result = JsonFlattener().flatten({"_comment": "ignore me", "x": 1})
    assert "_comment" not in result
    assert result["x"] == 1


def test_custom_separator():
    result = JsonFlattener(separator=".").flatten({"home": {"score": 108}})
    assert "home.score" in result


def test_max_depth_stores_raw_subtree():
    deep = {"a": {"b": {"c": {"d": "leaf"}}}}
    result = JsonFlattener(max_depth=2).flatten(deep)
    # depth 2 is reached at "a_b_c" — the subtree {"d": "leaf"} is stored raw
    assert "a_b_c" in result
    assert result["a_b_c"] == {"d": "leaf"}


def test_prefix_prepended_to_all_keys():
    result = JsonFlattener().flatten({"score": 5}, prefix="home")
    assert result["home_score"] == 5


# ──────────────────────────────────────────────────────────────────────────────
# JsonFlattener — full sports fixture
# ──────────────────────────────────────────────────────────────────────────────


def test_v1_fixture_home_away_scores(game_v1):
    flat = JsonFlattener().flatten(game_v1)
    assert flat["home_score"] == 108
    assert flat["away_score"] == 103


def test_v1_fixture_league_fields(game_v1):
    flat = JsonFlattener().flatten(game_v1)
    assert flat["league_name"] == "NBA"
    assert flat["league_region"] == "West"
    assert flat["league_season"] == "2024-25"


def test_v2_fixture_region_wraps_league(game_v2):
    flat = JsonFlattener().flatten(game_v2)
    # In v2 the nesting is flipped: region.league.name
    assert flat["region_league_name"] == "NBA"
    assert flat["region_name"] == "West"
    assert flat["region_league_season"] == "2024-25"
    # The raw v2 flat must NOT have league_name at the top level
    assert "league_name" not in flat


def test_v1_v2_raw_keys_differ(game_v1, game_v2):
    """Before resolution, v1 and v2 produce different keys for league data."""
    v1_flat = JsonFlattener().flatten(game_v1)
    v2_flat = JsonFlattener().flatten(game_v2)
    assert "league_name" in v1_flat
    assert "league_name" not in v2_flat
    assert "region_league_name" in v2_flat
    assert "region_league_name" not in v1_flat


# ──────────────────────────────────────────────────────────────────────────────
# SchemaResolver
# ──────────────────────────────────────────────────────────────────────────────


def test_resolver_renames_key():
    resolver = SchemaResolver({"home_team": "home_team_name"})
    result = resolver.resolve({"home_team": "Lakers", "home_score": 108})
    assert "home_team_name" in result
    assert "home_team" not in result
    assert result["home_score"] == 108  # unchanged


def test_resolver_passthrough_unknown_keys():
    resolver = SchemaResolver({"a": "b"})
    result = resolver.resolve({"x": 1, "y": 2})
    assert result == {"x": 1, "y": 2}


# ──────────────────────────────────────────────────────────────────────────────
# Equivalence — THE SPORTS PUNCHLINE
# ──────────────────────────────────────────────────────────────────────────────


def test_v1_v2_resolve_to_same_canonical_league_data(game_v1, game_v2, resolver):
    """
    After flattening + resolving, both feed versions must agree on
    league_name, region_name, and season regardless of which nesting
    order the vendor used.

    This is the sports equivalent of test_both_adapters_produce_equal_trial_records.
    """
    v1_record = flatten_to_record(game_v1, "G001", "nba_games", resolver=resolver)
    v2_record = flatten_to_record(game_v2, "G001", "nba_games", resolver=resolver)

    for canonical_key in ("league_name", "region_name", "season"):
        assert v1_record.get(canonical_key) == v2_record.get(canonical_key), (
            f"Canonical key {canonical_key!r} differs: "
            f"v1={v1_record.get(canonical_key)!r}, v2={v2_record.get(canonical_key)!r}"
        )


def test_v1_v2_resolve_to_same_game_scores(game_v1, game_v2, resolver):
    """Game scores must be identical regardless of feed version."""
    v1_record = flatten_to_record(game_v1, "G001", "nba_games", resolver=resolver)
    v2_record = flatten_to_record(game_v2, "G001", "nba_games", resolver=resolver)

    for score_key in ("home_score", "away_score",
                      "periods_1_home_score", "periods_4_away_score"):
        assert v1_record.get(score_key) == v2_record.get(score_key), (
            f"Score field {score_key!r} differs between v1 and v2"
        )


# ──────────────────────────────────────────────────────────────────────────────
# FlatRecord
# ──────────────────────────────────────────────────────────────────────────────


def test_flat_record_get_and_len():
    rec = FlatRecord("G001", "nba", {"home_score": 108, "away_score": 103})
    assert rec.get("home_score") == 108
    assert rec.get("missing", -1) == -1
    assert len(rec) == 2


def test_flat_record_keys():
    rec = FlatRecord("G001", "nba", {"a": 1, "b": 2})
    assert set(rec.keys()) == {"a", "b"}


# ──────────────────────────────────────────────────────────────────────────────
# FlatDuckDBSink
# ──────────────────────────────────────────────────────────────────────────────


def test_flat_sink_creates_parquet(tmp_path, game_v1, resolver):
    record = flatten_to_record(game_v1, "G001", "nba_games", resolver=resolver)
    sink = FlatDuckDBSink(output_dir=tmp_path)
    summary = sink.write(record)

    assert Path(summary["path"]).exists()
    assert summary["record_id"] == "G001"
    assert summary["fields_written"] == len(record)


def test_flat_sink_scores_round_trip(tmp_path, game_v1, resolver):
    record = flatten_to_record(game_v1, "G001", "nba_games", resolver=resolver)
    sink = FlatDuckDBSink(output_dir=tmp_path)
    sink.write(record)

    path = str(tmp_path / "nba_games" / "G001.parquet")
    row = duckdb.sql(
        f"SELECT home_score, away_score FROM read_parquet('{path}')"
    ).fetchone()

    assert row == (108, 103)


def test_flat_sink_both_versions_queryable(tmp_path, game_v1, game_v2, resolver):
    """
    Both feed versions write to the same source directory.
    Because SchemaResolver normalised the keys, both Parquet files have the
    same schema and can be queried together with a wildcard.
    """
    sink = FlatDuckDBSink(output_dir=tmp_path)
    sink.write(flatten_to_record(game_v1, "G001_v1", "nba_games", resolver=resolver))
    sink.write(flatten_to_record(game_v2, "G001_v2", "nba_games", resolver=resolver))

    wildcard = str(tmp_path / "nba_games" / "*.parquet")

    rows = duckdb.sql(
        f"SELECT record_id, league_name, home_score "
        f"FROM read_parquet('{wildcard}') ORDER BY record_id"
    ).fetchall()

    assert len(rows) == 2
    # Both records must agree on league_name (the whole point of the resolver)
    assert rows[0][1] == rows[1][1] == "NBA"
    # Both records must agree on home_score
    assert rows[0][2] == rows[1][2] == 108
