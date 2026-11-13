#!/usr/bin/env python3
"""Validate the material envelope and local evidence links; never predict a score."""
import argparse
import json
from pathlib import Path
import re
import math


def strict_load(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError("duplicate JSON key: "+key)
            result[key] = value
        return result
    def invalid(value): raise ValueError("nonfinite JSON: "+value)
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    def finite(value):
        if isinstance(value, float): assert math.isfinite(value), "overflowed JSON number"
        elif isinstance(value, dict):
            for child in value.values(): finite(child)
        elif isinstance(value, list):
            for child in value: finite(child)
    finite(data)
    return data


def pointer(data, path):
    if path == "": return data
    assert path.startswith("/"), "RFC6901 pointer must start with /"
    assert not re.search(r"~(?![01])", path), "invalid pointer escape"
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(data, list):
            assert re.fullmatch(r"0|[1-9][0-9]*", part), "invalid array index"
            data = data[int(part)]
        else: data = data[part]
    return data


def validate(path):
    raw = Path(path).read_bytes()
    assert len(raw) <= 128*1024, "128 KiB material limit"
    data = strict_load(raw)
    assert isinstance(data, dict), "top-level object required"
    text = raw.decode("utf-8")
    assert not re.search(r"(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{16,}", text), "possible credential"
    # Optional local links are NOT a mandatory official material schema.
    seen = set()
    for link in data.get("evidence_links", []):
        value = pointer(data, link["pointer"])
        fragment = link["quote"]
        assert isinstance(value, str) and 0 < len(fragment) <= 240
        assert fragment in value, "quote is not an exact substring"
        key = (link["pointer"], fragment)
        assert key not in seen, "duplicate evidence link"
        seen.add(key)
    return {"valid": True, "bytes": len(raw), "links": len(seen),
            "synthetic": data.get("synthetic", False),
            "student_learning_evidence_incomplete": data.get("student_learning_evidence_incomplete", "not assessed"),
            "scope": "format/provenance-link checks only; not official scoring or authorship certification"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.file), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
