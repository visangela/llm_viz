#!/usr/bin/env python3
"""
抓取 LLM 数据并生成 data/models.json

数据源:
  1. OpenRouter /api/v1/models
     模型列表 / 名称 / 创建时间 / 上下文长度 / 输入输出价格
  2. ai-model-index 镜像（每日同步 Artificial Analysis）
     · data/llm/aa-intelligence.json  → Intelligence Index
     · data/llm/aa-coding.json        → Coding Index

匹配策略（优先级从高到低）:
  a. 手动别名表 data/model_aliases.json
  b. OpenRouter slug 与 AA slug 精确匹配
  c. 归一化模型名模糊匹配（去掉括号、版本号、标点）

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
AA_INTELLIGENCE_URL = "https://raw.githubusercontent.com/EvanZhouDev/ai-model-index/main/data/llm/aa-intelligence.json"
AA_CODING_URL = "https://raw.githubusercontent.com/EvanZhouDev/ai-model-index/main/data/llm/aa-coding.json"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(BASE_DIR, "..", "data", "models.json")
OVERRIDE_PATH = os.path.join(BASE_DIR, "..", "data", "benchmarks_override.json")
ALIASES_PATH = os.path.join(BASE_DIR, "..", "data", "model_aliases.json")

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
    "snowflake": "Snowflake", "servicenow": "ServiceNow",
    "swiss-ai": "Swiss AI Initiative", "kwai-kat": "KwaiKAT",
    "lg": "LG AI Research", "naver": "Naver", "prime-intellect": "Prime Intellect",
    "openbmb": "OpenBMB", "tii": "TII UAE", "arcee-ai": "Arcee AI",
    "inception": "Inception", "longcat": "LongCat",
    "multiverse": "Multiverse Computing", "thinkingmachines": "Thinking Machines",
    "trillionlabs": "Trillion Labs", "sarvam": "Sarvam", "nanbeige": "Nanbeige",
    "ai9stars": "AI9Stars", "apodex": "Apodex", "reflection": "Reflection",
    "korea-telecom": "Korea Telecom", "china-mobile": "China Mobile",
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


def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] 读取 {path} 失败: {e}", file=sys.stderr)
        return default if default is not None else {}


def load_aa_scores(url, metric_key):
    """从 ai-model-index 镜像拉取某一指标的分数，返回 {slug: score} 和 {norm_name: score}"""
    try:
        data = http_get_json(url, timeout=20)
    except Exception as e:
        print(f"[warn] 拉取 {metric_key} 失败: {e}", file=sys.stderr)
        return {}, {}

    models = data.get("models") or []
    if not isinstance(models, list):
        return {}, {}

    by_slug = {}
    by_norm = {}

    for m in models:
        if not isinstance(m, dict):
            continue
        score = m.get("score")
        if score is None:
            continue
        try:
            score = float(score)
        except (ValueError, TypeError):
            continue

        slug = (m.get("slug") or "").strip()
        name = (m.get("name") or "").strip()

        if slug:
            by_slug[slug] = max(score, by_slug.get(slug, score))

        if name:
            norm = normalize_name(name)
            if norm:
                by_norm[norm] = max(score, by_norm.get(norm, score))

    print(f"      {metric_key}: slug {len(by_slug)} 个，norm {len(by_norm)} 个")
    return by_slug, by_norm


def match_scores(openrouter_id, aa_by_slug, aa_by_norm, aliases):
    """
    多级匹配：
      1. 手动别名表（OpenRouter id → AA slug）
      2. OpenRouter 后半段与 AA slug 精确匹配
      3. 归一化名称模糊匹配
    返回分数或 None
    """
    # 1. 手动别名
    alias = aliases.get(openrouter_id)
    if alias and alias in aa_by_slug:
        return aa_by_slug[alias]

    # 2. slug 精确匹配
    short_slug = openrouter_id.split("/", 1)[-1] if "/" in openrouter_id else openrouter_id
    if short_slug in aa_by_slug:
        return aa_by_slug[short_slug]

    # 3. 归一化模糊匹配
    norm = normalize_name(short_slug)
    if norm and norm in aa_by_norm:
        return aa_by_norm[norm]

    return None


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

    # 直接从 OpenRouter 返回的 benchmarks 字段中读取 AA 分数
    benchmarks = m.get("benchmarks") or {}
    aa = benchmarks.get("artificial_analysis") or {}

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
        # 关键改动：直接从 OpenRouter 读取分数
        "intelligence_score": aa.get("intelligence_index"),
        "coding_score": aa.get("coding_index"),
        "hle_score": None,  # OpenRouter 不直接提供 HLE
    }


def apply_override(model, override):
    """人工 override 作为最后兜底"""
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


def main():
    print("[1/5] 拉取 OpenRouter 模型列表…")
    data = http_get_json(OPENROUTER_URL)
    raw = data.get("data", [])
    print(f"      {len(raw)} 条")

    print("[2/5] 拉取 Artificial Analysis 分数（via ai-model-index）…")
    intel_slug, intel_norm = load_aa_scores(AA_INTELLIGENCE_URL, "intelligence")
    coding_slug, coding_norm = load_aa_scores(AA_CODING_URL, "coding")

    print("[3/5] 加载别名与人工 override…")
    aliases = load_json(ALIASES_PATH, default={})
    override = load_json(OVERRIDE_PATH, default={})
    print(f"      别名 {len(aliases)} 条，override {len(override)} 条")

    print("[4/5] 合并分数…")
    models = []
    match_stats = {"alias": 0, "slug": 0, "norm": 0, "miss": 0}

    for m in raw:
        try:
            pm = parse_model(m)
            if not pm or not pm["created"]:
                continue

            # Intelligence
            if pm["id"] in aliases and aliases[pm["id"]] in intel_slug:
                pm["intelligence_score"] = intel_slug[aliases[pm["id"]]]
                match_stats["alias"] += 1
            elif (s := match_scores(pm["id"], intel_slug, intel_norm, {})) is not None:
                pm["intelligence_score"] = s
                match_stats["slug" if "/" in pm["id"] and pm["id"].split("/", 1)[-1] in intel_slug else "norm"] += 1
            else:
                match_stats["miss"] += 1

            # Coding（独立匹配，因为某些模型可能只有其一）
            if pm["id"] in aliases and aliases[pm["id"]] in coding_slug:
                pm["coding_score"] = coding_slug[aliases[pm["id"]]]
            else:
                s = match_scores(pm["id"], coding_slug, coding_norm, {})
                if s is not None:
                    pm["coding_score"] = s

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
    with_coding = sum(1 for m in models if m["coding_score"] is not None)

    print(f"      总模型 {len(models)} 个")
    print(f"      Intelligence 覆盖 {with_intel} 个，Coding 覆盖 {with_coding} 个")
    print(f"      匹配方式分布: alias={match_stats['alias']}, "
          f"slug={match_stats['slug']}, norm={match_stats['norm']}, miss={match_stats['miss']}")

    print("[5/5] 写入 JSON…")
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "openrouter + artificial-analysis (via ai-model-index)",
        "total": len(models),
        "with_intelligence": with_intel,
        "with_coding": with_coding,
        "models": models,
    }
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"      已写入 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()