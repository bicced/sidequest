"""Tests for the parts that actually went wrong while building this.

No network. The mapper is exercised against a fake completion function, which
is the only way to reproduce the failure modes that matter -- a model echoing
its input back, renumbering a batch, or wrapping the array in an object.

Run: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sidequest import catalog, client, gateways, ledger, mapper


# --------------------------------------------------------------- parsing --

class TestLoads(unittest.TestCase):
    """Small models fence, prefix and wrap. A strict parser loses usable data."""

    def test_plain(self):
        self.assertEqual(client.loads('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(client.loads('```json\n{"a": 1}\n```'), {"a": 1})

    def test_fenced_without_language(self):
        self.assertEqual(client.loads('```\n{"a": 1}\n```'), {"a": 1})

    def test_prose_prefixed(self):
        self.assertEqual(client.loads('Sure! Here you go:\n{"a": 1}'), {"a": 1})

    def test_bare_array(self):
        self.assertEqual(client.loads('[{"a": 1}]'), [{"a": 1}])

    def test_array_after_prose(self):
        self.assertEqual(client.loads('Results:\n[{"a": 1}]'), [{"a": 1}])

    def test_empty_and_garbage(self):
        self.assertIsNone(client.loads(""))
        self.assertIsNone(client.loads("no json here at all"))


# ------------------------------------------------------------------ cost --

class TestCost(unittest.TestCase):
    """The bug this project was born from: the charge is not called `cost`."""

    GW = {"price": (1.0, 2.0)}

    def test_nanogpt_pricing_is_measured(self):
        raw = {"x_nanogpt_pricing": {"amount": 0.0000011}, "usage": {}}
        amount, measured = client.cost(raw, self.GW)
        self.assertAlmostEqual(amount, 0.0000011)
        self.assertTrue(measured)

    def test_usage_cost_is_measured(self):
        amount, measured = client.cost({"usage": {"cost": 0.25}}, self.GW)
        self.assertAlmostEqual(amount, 0.25)
        self.assertTrue(measured)

    def test_falls_back_to_modelled_and_says_so(self):
        raw = {"usage": {"prompt_tokens": 1_000_000,
                         "completion_tokens": 1_000_000}}
        amount, measured = client.cost(raw, self.GW)
        self.assertAlmostEqual(amount, 3.0)      # 1.0 in + 2.0 out
        self.assertFalse(measured)

    def test_zero_is_still_measured(self):
        # A genuinely free call must not be mistaken for "no data".
        amount, measured = client.cost(
            {"x_nanogpt_pricing": {"amount": 0.0}, "usage": {}}, self.GW)
        self.assertEqual(amount, 0.0)
        self.assertTrue(measured)


# --------------------------------------------------------------- catalog --

class TestPriceNormalisation(unittest.TestCase):
    """Gateways quote per-million and per-token. Confusing them is 1e6 wrong."""

    def test_per_million_declared(self):
        m = {"pricing": {"prompt": 0.10, "unit": "per_million_tokens"}}
        self.assertAlmostEqual(catalog._price(m, "prompt", "input"), 0.10)

    def test_per_token_inferred(self):
        m = {"pricing": {"prompt": 0.0000001}}      # OpenRouter style
        self.assertAlmostEqual(catalog._price(m, "prompt", "input"), 0.1)

    def test_missing_pricing(self):
        self.assertEqual(catalog._price({}, "prompt", "input"), 0.0)

    def test_string_price(self):
        m = {"pricing": {"prompt": "0.5", "unit": "per_million_tokens"}}
        self.assertAlmostEqual(catalog._price(m, "prompt", "input"), 0.5)


class TestSearch(unittest.TestCase):
    ROWS = [
        {"id": "cheap", "name": "cheap", "in": 0.01, "out": 0.05,
         "context": 8000, "vision": False},
        {"id": "mid", "name": "mid", "in": 0.10, "out": 0.40,
         "context": 200000, "vision": True},
        {"id": "dear", "name": "dear", "in": 3.00, "out": 15.0,
         "context": 200000, "vision": False},
    ]

    def test_sorted_by_total_price(self):
        self.assertEqual([r["id"] for r in catalog.search(self.ROWS)],
                         ["cheap", "mid", "dear"])

    def test_max_price_filters_on_output(self):
        self.assertEqual([r["id"] for r in catalog.search(self.ROWS, max_price=0.5)],
                         ["cheap", "mid"])

    def test_context_floor_and_vision(self):
        self.assertEqual(
            [r["id"] for r in catalog.search(self.ROWS, min_context=100000)],
            ["mid", "dear"])
        self.assertEqual([r["id"] for r in catalog.search(self.ROWS, vision=True)],
                         ["mid"])


# ---------------------------------------------------------------- mapper --

class TestRowExtraction(unittest.TestCase):
    def test_bare_list(self):
        self.assertEqual(mapper._rows([{"i": 0}]), [{"i": 0}])

    def test_results_wrapper(self):
        # json_object mode forbids a top-level array, so this is the shape the
        # contract actually asks for.
        self.assertEqual(mapper._rows({"results": [{"i": 0}]}), [{"i": 0}])

    def test_arbitrary_wrapper_key(self):
        self.assertEqual(mapper._rows({"verdicts": [{"i": 0}]}), [{"i": 0}])

    def test_single_object(self):
        self.assertEqual(mapper._rows({"i": 0}), [{"i": 0}])

    def test_nothing(self):
        self.assertEqual(mapper._rows(None), [])


class TestArraySchema(unittest.TestCase):
    def test_lifts_per_item_schema_and_requires_index(self):
        got = mapper._array_schema({
            "type": "object",
            "properties": {"verdict": {"type": "string"}},
            "required": ["verdict"]})
        item = got["properties"]["results"]["items"]
        self.assertIn("i", item["properties"])
        self.assertEqual(item["required"], ["i", "verdict"])

    def test_none_passes_through(self):
        self.assertIsNone(mapper._array_schema(None))


def fake_complete(replies):
    """Return a client.complete stand-in that serves canned replies in order."""
    calls = {"n": 0}

    def _complete(gw, messages, **kw):
        i = min(calls["n"], len(replies) - 1)
        calls["n"] += 1
        payload = replies[i]
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return {"text": text, "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "cost": 0.001, "measured": True, "model": "fake", "raw": {}}

    _complete.calls = calls
    return _complete


class TestBatchAlignment(unittest.TestCase):
    GW = {"name": "fake", "model": "fake", "price": (0, 0)}

    def run_map(self, items, replies, **kw):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.jsonl"
            with mock.patch.object(client, "complete", fake_complete(replies)), \
                 mock.patch.object(mapper.ledger, "record", lambda *a, **k: None):
                stats = mapper.run(self.GW, items, "do the thing", out,
                                   workers=1, **kw)
            rows = [json.loads(l) for l in out.read_text().splitlines()] \
                if out.exists() else []
        return stats, rows

    def test_local_indices_map_back_to_absolute(self):
        """The model sees 0..n-1; results must land on the real item numbers."""
        items = [f"item {i}" for i in range(6)]
        reply = {"results": [{"i": 0, "v": "a"}, {"i": 1, "v": "b"},
                             {"i": 2, "v": "c"}]}
        stats, rows = self.run_map(items, [reply], batch=3)
        self.assertEqual(stats["ok"], 6)
        self.assertEqual(sorted(r["i"] for r in rows), list(range(6)))

    def test_echoed_input_is_rejected_by_require(self):
        """A reply that parses cleanly but answers nothing must not be written."""
        items = ["a", "b"]
        echo = {"results": [{"i": 0, "title": "a"}, {"i": 1, "title": "b"}]}
        stats, rows = self.run_map(items, [echo], batch=2, require=["verdict"])
        self.assertEqual(rows, [])
        self.assertEqual(stats["ok"], 0)
        self.assertEqual(stats["failed"], 2)

    def test_without_require_the_same_reply_is_accepted(self):
        # Documents why --require matters: nothing else catches this.
        items = ["a", "b"]
        echo = {"results": [{"i": 0, "title": "a"}, {"i": 1, "title": "b"}]}
        stats, _ = self.run_map(items, [echo], batch=2)
        self.assertEqual(stats["ok"], 2)

    def test_short_batch_is_split_and_retried(self):
        """One result for a two-item batch: split, then both arrive."""
        items = ["a", "b"]
        replies = [
            {"results": [{"i": 0, "v": "x"}]},          # only one of two
            {"results": [{"i": 0, "v": "x"}]},          # split half 1
            {"results": [{"i": 0, "v": "y"}]},          # split half 2
        ]
        stats, rows = self.run_map(items, replies, batch=2)
        self.assertEqual(sorted(r["i"] for r in rows), [0, 1])
        self.assertEqual(stats["failed"], 0)

    def test_out_of_range_index_is_ignored(self):
        items = ["a"]
        stats, rows = self.run_map(items, [{"results": [{"i": 99, "v": "x"}]}],
                                   batch=1, require=["v"])
        self.assertEqual(rows, [])

    def test_fail_fast_stops_a_hopeless_model(self):
        """Three failed batches with nothing usable aborts the run."""
        items = [f"item {i}" for i in range(200)]
        stats, _ = self.run_map(items, ["not json at all"], batch=10,
                                require=["verdict"])
        self.assertTrue(stats["aborted"])
        self.assertEqual(stats["ok"], 0)

    def test_resume_skips_completed_items(self):
        items = ["a", "b", "c"]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.jsonl"
            out.write_text(json.dumps({"i": 0, "v": "done"}) + "\n")
            reply = {"results": [{"i": 0, "v": "x"}, {"i": 1, "v": "y"}]}
            with mock.patch.object(client, "complete", fake_complete([reply])), \
                 mock.patch.object(mapper.ledger, "record", lambda *a, **k: None):
                stats = mapper.run(self.GW, items, "x", out, batch=10, workers=1)
            self.assertEqual(stats["skipped"], 1)
            rows = [json.loads(l) for l in out.read_text().splitlines()]
            self.assertEqual(sorted(r["i"] for r in rows), [0, 1, 2])


class TestLoadItems(unittest.TestCase):
    def test_jsonl_json_and_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.jsonl"
            p.write_text('{"x": 1}\n{"x": 2}\n')
            self.assertEqual(mapper.load_items(p), [{"x": 1}, {"x": 2}])

            p = Path(d) / "b.json"
            p.write_text('[{"x": 1}]')
            self.assertEqual(mapper.load_items(p), [{"x": 1}])

            p = Path(d) / "c.txt"
            p.write_text("one\ntwo\n\n")
            self.assertEqual(mapper.load_items(p), ["one", "two"])


# ---------------------------------------------------------------- ledger --

class TestLedger(unittest.TestCase):
    def test_roundtrip_and_estimate_split(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"SIDEQUEST_HOME": d}):
                ledger.record("nanogpt", "m1", 0.10, True,
                              {"prompt_tokens": 5, "completion_tokens": 2})
                ledger.record("nanogpt", "m1", 0.20, True, {})
                ledger.record("groq", "m2", 0.05, False, {})
                s = ledger.summarise(ledger.read())
        self.assertEqual(s["calls"], 3)
        self.assertAlmostEqual(s["total"], 0.35)
        self.assertAlmostEqual(s["estimated_portion"], 0.05)
        self.assertEqual(s["by_model"]["nanogpt/m1"]["calls"], 2)

    def test_survives_a_torn_line(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"SIDEQUEST_HOME": d}):
                ledger.record("g", "m", 0.1, True, {})
                with open(ledger.ledger_path(), "a") as f:
                    f.write('{"ts": 1, "cost": bro\n')   # killed mid-write
                self.assertEqual(len(ledger.read()), 1)


# -------------------------------------------------------------- gateways --

class TestResolve(unittest.TestCase):
    def test_key_from_env(self):
        with mock.patch.dict(os.environ, {"NANOGPT_API_KEY": "k"}, clear=True):
            gw = gateways.resolve("nanogpt")
        self.assertEqual(gw["api_key"], "k")
        self.assertTrue(gw["reports_cost"])

    def test_missing_key_is_an_actionable_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(gateways.ConfigError) as cm:
                gateways.resolve("nanogpt")
        self.assertIn("NANOGPT_API_KEY", str(cm.exception))

    def test_unknown_gateway_lists_the_known_ones(self):
        with self.assertRaises(gateways.ConfigError) as cm:
            gateways.resolve("nope")
        self.assertIn("openrouter", str(cm.exception))

    def test_base_url_override_needs_no_preset(self):
        with mock.patch.dict(os.environ, {"SIDEQUEST_API_KEY": "k"}, clear=True):
            gw = gateways.resolve("custom", "m", "https://x/v1")
        self.assertEqual(gw["base_url"], "https://x/v1")

    def test_ollama_needs_no_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            gw = gateways.resolve("ollama")
        self.assertEqual(gw["api_key"], "local")

    def test_env_defaults(self):
        with mock.patch.dict(os.environ, {"NANOGPT_API_KEY": "k",
                                          "SIDEQUEST_MODEL": "pinned"}, clear=True):
            self.assertEqual(gateways.resolve("nanogpt")["model"], "pinned")


if __name__ == "__main__":
    unittest.main()
