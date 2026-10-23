#!/usr/bin/env python3
"""
抓取 LLM 数据并生成 data/models.json

数据源:
  1. OpenRouter /api/v1/models
     模型列表 / 名称 / 创建时间 / 上下文长度 / 输入输出价格
  2. llm-value-rankings/data/models.json
     Intelligence / Coding / HLE 等 benchmark 分数

匹配策略:
  将两边模型名归一化（小写、去括号、去版本号、去标点）后按名称匹配；
  同一系列多个档位取最高分。

补充:
  data/benchmarks_override.json 可人工兜底补充分数（可选）。
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
LLM_RANKINGS_URL = "https://raw.githubusercontent.com/yyh-001/llm-value-rankings/main/data/models.json"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(BASE_DIR, "..", "data", "models.json")
OVERRIDE_PATH = os.path.join(BASE_DIR, "..", "data", "benchmarks_override.json")

PROVIDER_DISPLAY = {
    "anthropic": "Anthropic", "openai": "OpenAI", "google": "Google",
    "meta-llama": "Meta", "meta": "Meta", "mistralai": "Mistral",
    "deepseek": "DeepSeek", "qwen": "Alibaba", "x-ai": "SpaceXAI",
    "cohere": "Cohere", "nvidia": "NVIDIA", "microsoft": "Microsoft",
    "amazon": "Amazon", "xiaomi": "Xiaomi", "moonshotai": "Kimi",
    "z-ai": "Z AI", "minimax": "MiniMax", "stepfun": "StepFun",
    "inclusionai": "InclusionAI", "upstage": "Upstage",
    "perplexity": "Perplexity", "ibm": "IBM", "baidu": "Baidu",
    "tencent": "Tencent", "bytedance": "ByteDance", "nex-agi": "Nex AGI",
    "sapiens-ai": "Sapiens AI", "openchat": "OpenChat", "databricks": "Databricks",
    "liquid": "Liquid AI", "ai21": "AI21 Labs", "reka": "Reka AI",
    "snowflake": "Snowflake", "servicenow": "ServiceNow", "swiss-ai": "Swiss AI Initiative",
    "kwai-kat": "KwaiKAT", "lg": "LG AI Research", "naver": "Naver",
    "prime-intellect": "Prime Intellect", "openbmb": "OpenBMB", "tii": "TII UAE",
    "arcee-ai": "Arcee AI", "inception": "Inception", "longcat": "LongCat",
    "multiverse": "Multiverse Computing", "thinkingmachines": "Thinking Machines",
    "trillionlabs": "Trillion Labs", "sarvam": "Sarvam", "nanbeige": "Nanbeige",
    "ai9stars": "AI9Stars", "apodex": "Apodex", "reflection": "Reflection",
    "nex": "Nex AGI", "korea-telecom": "Korea Telecom", "china-mobile": "China Mobile",
    "motif": "Motif Technologies", "anthropic-ai": "Anthropic",
}


def http_get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": "llm-timeline-fetcher/1.0 (+https://github.com/)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalize_name(s):
    """归一化模型名：小写、去括号、去版本号、去标点，仅保留字母数字"""
    s = (s or "").lower()
    s = re.sub(r"\([^)]*\)", " ", s)                # 去括号内容
    s = re.sub(r"[^a-z0-9]+", " ", s)               # 非字母数字转空格
    s = re.sub(r"\b\d+(?:\.\d+)*\b", " ", s)        # 去版本号 4.5 / 3 / 6.1
    s = re.sub(r"\s+", " ", s).strip()
    return s


def get_num(m, keys):
    for k in keys:
        v = m.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (ValueError, TypeError):
            continue
    return None


def _max_opt(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def fetch_llm_rankings_scores():
    """从 llm-value-rankings 拉取分数，按归一化名称聚合"""
    try:
        data = http_get_json(LLM_RANKINGS_URL, timeout=20)
    except Exception as e:
        print(f"[warn] 拉取 llm-value-rankings 失败: {e}", file=sys.stderr)
        return {}

    raw_models = data.get("models") or data.get("data") or []
    if not isinstance(raw_models, list):
        print("[warn] llm-value-rankings 返回格式异常", file=sys.stderr)
        return {}

    scores = {}
    for m in raw_models:
        if not isinstance(m, dict):
            continue
        name = m.get("name") or m.get("model") or m.get("id") or ""
        key = normalize_name(name)
        if not key:
            continue
        score = {
            "intelligence": get_num(m, ["intelligence_score", "intelligence",
                                        "Intelligence Index", "intelligence_index"]),
            "coding": get_num(m, ["coding_score", "coding",
                                  "Coding Index", "coding_index"]),
            "hle": get_num(m, ["hle_score", "hle", "HLE"]),
        }
        if score["intelligence"] is None and score["coding"] is None and score["hle"] is None:
            continue
        prev = scores.get(key)
        if prev is None:
            scores[key] = score
        else:
            scores[key] = {
                "intelligence": _max_opt(prev["intelligence"], score["intelligence"]),
                "coding": _max_opt(prev["coding"], score["coding"]),
                "hle": _max_opt(prev["hle"], score["hle"]),
            }

    print(f"      从 llm-value-rankings 获取 {len(scores)} 个系列分数")
    return scores


def match_score(openrouter_id, scores_by_name):
    model_part = openrouter_id.split("/", 1)[-1] if "/" in openrouter_id else openrouter_id
    key = normalize_name(model_part)
    return scores_by_name.get(key)


def load_override():
    if not os.path.exists(OVERRIDE_PATH):
        return {}
    try:
        with open(OVERRIDE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] 读取 override 失败: {e}", file=sys.stderr)
        return {}


def apply_override(model, override):
    if not override:
        return model
    mid_lower = model["id"].lower()
    for key in sorted(override.keys(), key=len, reverse=True):
        if key.lower() in mid_lower:
            vals = override[key]
            if model["intelligence_score"] is None and vals.get("intelligence") is not None:
                model["intelligence_score"] = float(vals["intelligence"])
            if model["coding_score"] is None and vals.get("coding") is not None:
                model["coding_score"] = float(vals["coding"])
            if model["hle_score"] is None and vals.get("hle") is not None:
                model["hle_score"] = float(vals["hle"])
            return model
    return model


def parse_model(m):
    mid = (m.get("id") or "").strip()
    if not mid:
        return None
    name = m.get("name") or mid
    created = m.get("created")
    pricing = m.get("pricing") or {}
    prompt_price = float(pricing.get("prompt") or 0) * 1_000_000
    completion_price = float(pricing.get("completion") or 0) * 1_000_000
    provider_slug = mid.split("/")[0] if "/" in mid else "unknown"
    provider_display = PROVIDER_DISPLAY.get(provider_slug, provider_slug.replace("-", " ").title())
    return {
        "id": mid,
        "name": name,
        "provider": provider_slug,
        "provider_display": provider_display,
        "created": created,
        "pricing": {
            "prompt_per_mtok": round(prompt_price, 4),
            "completion_per_mtok": round(completion_price, 4),
        },
        "context_length": m.get("context_length", 0),
        "intelligence_score": None,
        "coding_score": None,
        "hle_score": None,
    }


def main():
    print("[1/4] 拉取 OpenRouter 模型列表…")
    data = http_get_json(OPENROUTER_URL)
    raw = data.get("data", [])
    print(f"      {len(raw)} 条")

    print("[2/4] 拉取 llm-value-rankings 分数…")
    rankings_scores = fetch_llm_rankings_scores()

    print("[3/4] 合并分数…")
    override = load_override()
    models = []
    matched = 0
    for m in raw:
        try:
            pm = parse_model(m)
            if not pm or not pm["created"]:
                continue
            score = match_score(pm["id"], rankings_scores)
            if score:
                pm["intelligence_score"] = score["intelligence"]
                pm["coding_score"] = score["coding"]
                pm["hle_score"] = score["hle"]
                matched += 1
            pm = apply_override(pm, override)
            models.append(pm)
        except Exception as e:
            print(f"[warn] 解析 {m.get('id')} 失败: {e}", file=sys.stderr)

    # 去重
    seen = set()
    unique = []
    for m in models:
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        unique.append(m)
    models = unique
    models.sort(key=lambda x: x["created"] or 0, reverse=True)

    with_intel = sum(1 for m in models if m["intelligence_score"] is not None)
    print(f"      匹配到分数: {matched} / {len(models)}，最终含 Intelligence 的 {with_intel} 个")

    print("[4/4] 写入 JSON…")
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "openrouter+llm-value-rankings",
        "total": len(models),
        "models": models,
    }
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"      已写入 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()