import json
import re


class OutputFormatError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def clean_content(content: str) -> str:
    # Qwen3 的服务可能把思考放在 content，而不是 reasoning_content。
    if content.lstrip().startswith("<think>") and "</think>" not in content:
        raise OutputFormatError("llm_incomplete_thinking", "模型思考输出未结束")
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", content, flags=re.DOTALL | re.IGNORECASE)
    return fence.group(1).strip() if fence else content


def parse_json(content: str):
    def reject_constant(value):
        raise ValueError(f"非标准 JSON 常量: {value}")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON 包含重复键")
            result[key] = value
        return result

    if not content:
        raise OutputFormatError("llm_empty_output", "模型未返回正文")
    try:
        return json.loads(content, parse_constant=reject_constant, object_pairs_hook=unique_keys)
    except (ValueError, RecursionError) as exc:
        raise OutputFormatError("llm_invalid_json", "模型输出不是有效 JSON") from exc
