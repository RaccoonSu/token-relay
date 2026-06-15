from app.utils import extract_usage


def test_extract_usage_none_input():
    assert extract_usage(None) is None


def test_extract_usage_no_usage_field():
    assert extract_usage({"content": []}) is None


def test_extract_usage_full():
    body = {"usage": {
        "input_tokens": 100,
        "cache_read_input_tokens": 50,
        "cache_creation_input_tokens": 30,
        "output_tokens": 200,
    }}
    result = extract_usage(body)
    assert result == {
        "input_tokens": 100,
        "cache_hit_tokens": 80,
        "output_tokens": 200,
        "total_tokens": 380,
    }


def test_extract_usage_missing_cache_fields():
    body = {"usage": {"input_tokens": 10, "output_tokens": 5}}
    result = extract_usage(body)
    assert result == {
        "input_tokens": 10,
        "cache_hit_tokens": 0,
        "output_tokens": 5,
        "total_tokens": 15,
    }


def test_extract_usage_zero_values():
    body = {"usage": {"input_tokens": 0, "output_tokens": 0}}
    result = extract_usage(body)
    assert result["total_tokens"] == 0
