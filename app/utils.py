def extract_usage(response_body: dict | None) -> dict | None:
    """从 Anthropic 响应体中提取 token 用量。

    返回包含 input_tokens / cache_hit_tokens / output_tokens / total_tokens 的字典；
    若无 usage 字段则返回 None。各数值字段缺失按 0 处理。
    """
    if not response_body or not isinstance(response_body, dict):
        return None
    usage = response_body.get("usage")
    if not usage:
        return None
    input_tokens = usage.get("input_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    cache_create = usage.get("cache_creation_input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    cache_hit = cache_read + cache_create
    # input_tokens 已不含缓存部分，真正的总输入需把缓存读+缓存写加上
    total_tokens = input_tokens + cache_read + cache_create + output_tokens
    return {
        "input_tokens": input_tokens,
        "cache_hit_tokens": cache_hit,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
