"""Parse structured model output without executing model-generated code."""
import ast
import json
import re


def model_json(text, *, object_response=False):
    if not isinstance(text, str) or len(text) > 200000:
        raise ValueError("모델 JSON 문자열 형식을 확인하세요.")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json|python)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        result = json.loads(text, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    except json.JSONDecodeError:
        # Some local servers ignore strict JSON schemas and return Python-style
        # quotes/booleans. literal_eval accepts data only, never calls or code.
        try:
            result = ast.literal_eval(text)
            result = json.loads(json.dumps(result, allow_nan=False))
        except (ValueError, SyntaxError, TypeError, RecursionError) as exc:
            raise ValueError("모델이 유효한 JSON 값을 반환하지 않았습니다.") from exc
    if object_response and not isinstance(result, dict):
        raise ValueError("모델 응답의 최상위 값은 JSON 객체여야 합니다.")
    return result


def normalize_operations(operations):
    if not isinstance(operations, list):
        raise ValueError("프로필 수정 목록 형식 오류")
    normalized = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("프로필 수정 항목 형식 오류")
        item = dict(operation)
        if item.get("op") != "remove":
            try:
                value = model_json(item.get("value_json"))
            except ValueError as exc:
                raise ValueError(f"{item.get('file')} {item.get('path')}: value_json 형식 오류 ({exc})") from exc
            item["value_json"] = json.dumps(value, ensure_ascii=False, allow_nan=False)
        normalized.append(item)
    return normalized
