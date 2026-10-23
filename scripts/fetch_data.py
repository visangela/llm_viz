#!/usr/bin/env python3
"""
抓取 LLM 数据并生成 data/models.json

数据源:
  - OpenRouter /api/v1/models  (免费、无需 key)
    · 模型列表 / 名称 / 创建时间 / 上下文长度
    · 输入输出价格（每百万 token）
    · 供应商 id 前缀

补充机制:
  - data/benchmarks_override.json
    用于补充 OpenRouter 未提供的 Intelligence / Coding / HLE 分数。
    匹配方式：模型 id 中包含 key（不区分大小写）。
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(BASE_DIR, "..", "data", "models.json")
OVERRIDE_PATH = os.path.join(BASE_DIR, "..", "data", "benchmarks_override.json")

PROVIDER_DISPLAY = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "meta-llama": "Meta",
    "mistralai": "Mistral",
    "deepseek": "DeepSeek",
    "qwen": "Alibaba",
    "x-ai": "SpaceXAI",
    "cohere": "Cohere",
    "nvidia": "NVIDIA",
    "microsoft": "Microsoft",
    "amazon": "Amazon",
    "xiaomi": "Xiaomi",
    "moonshotai": "Kimi",
    "z-ai": "Z AI",
    "minimax": "MiniMax",
    "stepfun": "StepFun",
    "inclusionai": "InclusionAI",
    "upstage": "Upstage",
    "perplexity": "Perplexity",
    "ibm": "IBM",
    "baidu": "Baidu",
    "tencent": "Tencent",
    "bytedance": "ByteDance",
    "bytedance-seed": "ByteDance",
    "nex-agi": "Nex AGI",
    "sapiens-ai": "Sapiens AI",
}


def http_get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": "llm-timeline-fetcher/1.0 (+https://github.com/)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_openrouter_models():
    data = http_get_json(OPENROUTER_MODELS_URL)
    return data.get("data", [])


def load_override():
    if not os.path.exists(OVERRIDE_PATH):
        return {}
    try:
        with open(OVERRIDE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] 读取 benchmarks_override 失败: {e}", file=sys.stderr)
        return {}


def parse_model(m):
    mid = (m.get("id") or "").strip()
    if not mid:
        return None

    name = m.get("name") or mid
    created = m.get("created")  # 秒级 Unix 时间戳
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
        # 以下字段由 override 补充（OpenRouter 不直接提供）
        "intelligence_score": None,
        "coding_score": None,
        "hle_score": None,
    }


def apply_override(model, override):
    mid_lower = model["id"].lower()
    # 最长匹配优先
    keys = sorted(override.keys(), key=len, reverse=True)
    for key in keys:
        if key.lower() in mid_lower:
            vals = override[key]
            if vals.get("intelligence") is not None:
                model["intelligence_score"] = float(vals["intelligence"])
            if vals.get("coding") is not None:
                model["coding_score"] = float(vals["coding"])
            if vals.get("hle") is not None:
                model["hle_score"] = float(vals["hle"])
            return model
    return model


def main():
    print("[1/3] 拉取 OpenRouter 模型列表…")
    raw = fetch_openrouter_models()
    print(f"      获取到 {len(raw)} 个原始条目")

    print("[2/3] 解析与补充…")
    override = load_override()
    if override:
        print(f"      加载 override 规则 {len(override)} 条")

    models = []
    for m in raw:
        try:
            pm = parse_model(m)
            if not pm:
                continue
            if not pm["created"]:
                continue
            pm = apply_override(pm, override)
            models.append(pm)
        except Exception as e:
            print(f"[warn] 解析 {m.get('id')} 失败: {e}", file=sys.stderr)

    # 去重（同一 id 保留一条）
    seen = set()
    unique = []
    for m in models:
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        unique.append(m)
    models = unique

    # 按 created 倒序
    models.sort(key=lambda x: x["created"] or 0, reverse=True)

    print(f"      最终保留 {len(models)} 个模型")

    print("[3/3] 写入 JSON…")
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "openrouter",
        "total": len(models),
        "models": models,
    }
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"      已写入 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()