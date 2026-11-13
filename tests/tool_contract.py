"""Meaningful negative controls for measurement and material validation helpers."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"tools"))
from compare import compare
from evidence import validate


class ToolContract(unittest.TestCase):
    def report(self):
        return {"passed": True, "workload": {"seed": 1}, "environment": {"memory": "2g"},
                "measurement": {"unit": "bytes"},
                "rounds": [{"round": i, "passed": True,
                            "write": {"T_seconds": 1, "B_bytes": 1048576, "M_bytes": 1048576},
                            "query": {"T_seconds": 1, "B_bytes": 1048576, "M_bytes": 1048576,
                                      "recall": {"filtered": 1, "unfiltered": 1}}}
                           for i in (1, 2, 3)]}

    def test_identical_score_and_median(self):
        base = self.report()
        self.assertEqual(compare(base, base)["median_E"], "0.500000")
        candidate = copy.deepcopy(base)
        candidate["rounds"][2]["query"]["T_seconds"] = 100
        self.assertEqual(compare(base, candidate)["median_E"], "0.500000")

    def test_reject_invalid_comparisons(self):
        base = self.report()
        mutations = [
            lambda r: r["rounds"].pop(),
            lambda r: r["rounds"][0].update(passed=False),
            lambda r: r["rounds"][0]["query"]["recall"].update(filtered=.94),
            lambda r: r["workload"].update(seed=2),
            lambda r: r["environment"].update(memory="4g"),
            lambda r: r["measurement"].update(unit="rss"),
            lambda r: r["rounds"][0]["write"].update(host_swap_io_delta={"pswpout": 1}),
        ]
        for mutate in mutations:
            candidate = copy.deepcopy(base); mutate(candidate)
            with self.assertRaises(AssertionError): compare(base, candidate)

    def test_material_negative_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"conversation.json"
            for invalid in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}',
                            b'[]', b'{"x":"'+b'x'*131072+b'"}'):
                path.write_bytes(invalid)
                with self.assertRaises((ValueError, AssertionError)): validate(path)
            data = {"synthetic": True, "conversations": [{"content": "actual test fragment"}],
                    "evidence_links": [{"pointer": "/conversations/0/content", "quote": "test fragment"}]}
            path.write_text(json.dumps(data))
            self.assertTrue(validate(path)["valid"])
            data["evidence_links"][0]["quote"] = "fabricated quotation"
            path.write_text(json.dumps(data))
            with self.assertRaises(AssertionError): validate(path)


if __name__ == "__main__":
    unittest.main()
