from app.shared_services.json_formatter import json_formatter


def test_cleans_code_fences():
    raw = '```json\n{"a": 1}\n```'
    assert json_formatter.parse(raw) == {"a": 1}


def test_extracts_json_from_preamble():
    raw = 'Sure, here is the JSON:\n{"a": 1, "b": [1,2,3]}'
    assert json_formatter.parse(raw) == {"a": 1, "b": [1, 2, 3]}
