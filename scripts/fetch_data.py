#!/usr/bin/env python3
"""
抓取 LLM 数据并生成 data/models.json

数据源策略（按优先级）:
  1. Artificial Analysis 官方免费 API（推荐，需要 AA_API_KEY）
     GET https://artificialanalysis.ai/api/v2/data/llms/models
     全量语言模型，包含 Intelligence / Coding 指数
  2. 回退：解析 public leaderboard 的内联数据（无需 key）
     模仿 llm-value-ranker 的做法，从排行榜页面提取 JSON

元数据（模型名、价格、创建时间）仍来自 OpenRouter。

匹配:
  以 AA 返回的 slug / name 为基准，用 OpenRouter 的模型 id 去匹配；
  支持 data/model_aliases.json 手动补充。

输出:
  data/models.json（结构不变，前端无需改动）
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
AA_API_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
AA_LEADERBOARD_URL = "https://artificialanalysis.ai/leaderboards/models"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATH = os.path.join(BASE_DIR, "..", "data", "models.json")
ALIASES_PATH = os.path.join(BASE_DIR, "..", "data", "model_aliases.json")
OVERRIDE_PATH = os.path.join(BASE_DIR, "..", "data", "benchmarks_override.json")

PROVIDER_DISPLAY = {
    "anthropic": "Anthropic", "openai": "OpenAI", "google": "Google",
    "meta-llama": "Meta", "meta": "Meta", "mistralai": "Mistral",
    "deepseek": "DeepSeek", "qwen": "Alibaba", "x-ai": "SpaceXAI",
    "cohere": "Cohere", "nvidia": "NVIDIA", "microsoft": "Microsoft",
    "amazon": "Amazon", "xiaomi": "Xiaomi", "moonshotai": "Kimi",
    "z-ai": "Z AI", "minimax": "MiniMax", "stepfun": "StepFun",
    "inclusionai": "InclusionAI", "upstage": "Upstage", "perplexity": "Perplexity",
    "ibm": "IBM", "baidu": "Baidu", "tencent": "Tencent", "bytedance": "ByteDance",
    "nex-agi": "Nex AGI", "sapiens-ai": "Sapiens AI", "openchat": "OpenChat",
    "databricks": "Databricks", "liquid": "Liquid AI", "ai21": "AI21 Labs",
    "reka": "Reka AI", "snowflake": "Snowflake", "servicenow": "ServiceNow",
    "swiss-ai": "Swiss AI Initiative", "kwai-kat": "KwaiKAT", "lg": "LG AI Research",
    "naver": "Naver", "prime-intellect": "Prime Intellect", "openbmb": "OpenBMB",
    "tii": "TII UAE", "arcee-ai": "Arcee AI", "inception": "Inception",
    "longcat": "LongCat", "multiverse": "Multiverse Computing",
    "thinkingmachines": "Thinking Machines", "trillionlabs": "Trillion Labs",
    "sarvam": "Sarvam", "nanbeige": "Nanbeige", "ai9stars": "AI9Stars",
    "apodex": "Apodex", "reflection": "Reflection", "korea-telecom": "Korea Telecom",
    "china-mobile": "China Mobile", "motif": "Motif Technologies",
}


def http_get_json(url, headers=None, timeout=30):
    h = {"User-Agent": "llm-timeline-fetcher/2.0 (+https://github.com/)"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_get_text(url, headers=None, timeout=30):
    h = {"User-Agent": "llm-timeline-fetcher/2.0 (+https://github.com/)"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def normalize_name(s):
    s = (s or "").lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\b\d+(?:\.\d+)*\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] 读取 {path} 失败: {e}", file=sys.stderr)
        return default if default is not None else {}


# ---------------------------------------------------------------
# AA 数据获取：优先官方 API，回退到解析公共排行榜
# ---------------------------------------------------------------
def fetch_aa_from_api(api_key):
    """调用 AA 免费 API，返回 {slug: {intelligence, coding}}"""
    print("      使用 Artificial Analysis 官方 API")
    try:
        data = http_get_json(AA_API_URL, headers={"x-api-key": api_key})
    except Exception as e:
        print(f"[warn] AA API 请求失败: {e}", file=sys.stderr)
        return {}

    items = data.get("data") or []
    if not isinstance(items, list):
        print("[warn] AA API 返回 data 不是数组", file=sys.stderr)
        return {}

    scores = {}
    for m in items:
        slug = (m.get("slug") or "").strip()
        if not slug:
            continue
        ev = m.get("evaluations") or {}
        intel = ev.get("artificial_analysis_intelligence_index")
        coding = ev.get("artificial_analysis_coding_index")
        if intel is None and coding is None:
            continue
        scores[slug] = {
            "intelligence": float(intel) if intel is not None else None,
            "coding": float(coding) if coding is not None else None,
        }
    print(f"      AA API 返回 {len(scores)} 个模型")
    return scores


def fetch_aa_from_leaderboard():
    """无 key 回退：解析公共排行榜页面内联 JSON"""
    print("      回退：解析 Artificial Analysis 公共排行榜")
    try:
        html = http_get_text(AA_LEADERBOARD_URL)
    except Exception as e:
        print(f"[warn] 排行榜页面请求失败: {e}", file=sys.stderr)
        return {}

    # 排行榜把数据塞在 <script id="__NEXT_DATA__"> 里
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  html, re.DOTALL)
    if not m:
        print("[warn] 未在页面中找到 __NEXT_DATA__", file=sys.stderr)
        return {}

    try:
        next_data = json.loads(m.group(1))
    except Exception as e:
        print(f"[warn] 解析 __NEXT_DATA__ 失败: {e}", file=sys.stderr)
        return {}

    # 递归查找包含 slug + intelligence 的对象列表
    found = {}

    def walk(obj):
        if isinstance(obj, dict):
            slug = obj.get("slug")
            if isinstance(slug, str) and slug:
                intel = obj.get("intelligence_index") or obj.get("artificial_analysis_intelligence_index")
                coding = obj.get("coding_index") or obj.get("artificial_analysis_coding_index")
                if intel is not None or coding is not None:
                    try:
                        found[slug] = {
                            "intelligence": float(intel) if intel is not None else None,
                            "coding": float(coding) if coding is not None else None,
                        }
                    except (ValueError, TypeError):
                        pass
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(next_data)
    print(f"      排行榜内联数据解析到 {len(found)} 个模型")
    return found


def fetch_aa_scores():
    api_key = os.environ.get("AA_API_KEY", "").strip()
    if api_key:
        scores = fetch_aa_from_api(api_key)
        if scores:
            return scores
        print("      API 无数据，回退到排行榜解析")
    return fetch_aa_from_leaderboard()


# ---------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------
def build_lookup(aa_scores):
    by_slug = dict(aa_scores)
    by_norm = {}
    for slug, sc in aa_scores.items():
        n = normalize_name(slug)
        if n:
            # 归一化后冲突时取 intelligence 更高的
            if n not in by_norm or (sc["intelligence"] or 0) > (by_norm[n]["intelligence"] or 0):
                by_norm[n] = sc
    return by_slug, by_norm


def match_scores(openrouter_id, by_slug, by_norm, aliases):
    alias = aliases.get(openrouter_id)
    if alias and alias in by_slug:
        return by_slug[alias]

    short = openrouter_id.split("/", 1)[-1] if "/" in openrouter_id else openrouter_id
    if short in by_slug:
        return by_slug[short]

    norm = normalize_name(short)
    if norm and norm in by_norm:
        return by_norm[norm]

    return None


# ---------------------------------------------------------------
# OpenRouter 模型解析
# ---------------------------------------------------------------
def parse_openrouter_model(m):
    mid = (m.get("id") or "").strip()
    if not mid:
        return None
    name = m.get("name") or mid
    created = m.get("created")
    pricing = m.get("pricing") or {}
    try:
        prompt = float(pricing.get("prompt") or 0) * 1_000_000
        completion = float(pricing.get("completion") or 0) * 1_000_000
    except (ValueError, TypeError):
        prompt = completion = 0.0

    provider_slug = mid.split("/")[0] if "/" in mid else "unknown"
    provider_display = PROVIDER_DISPLAY.get(provider_slug, provider_slug.replace("-", " ").title())

    return {
        "id": mid,
        "name": name,
        "provider": provider_slug,
        "provider_display": provider_display,
        "created": created,
        "pricing": {
            "prompt_per_mtok": round(prompt, 4),
            "completion_per_mtok": round(completion, 4),
        },
        "context_length": m.get("context_length", 0),
        "intelligence_score": None,
        "coding_score": None,
        "hle_score": None,
    }


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


def main():
    print("[1/4] 拉取 OpenRouter 模型列表…")
    data = http_get_json(OPENROUTER_URL)
    raw = data.get("data", [])
    print(f"      {len(raw)} 条")

    print("[2/4] 获取 Artificial Analysis 分数…")
    aa_scores = fetch_aa_scores()
    by_slug, by_norm = build_lookup(aa_scores)

    print("[3/4] 匹配并合并…")
    aliases = load_json(ALIASES_PATH, default={})
    override = load_json(OVERRIDE_PATH, default={})

    models = []
    stats = {"alias": 0, "slug": 0, "norm": 0, "miss": 0}

    for m in raw:
        try:
            pm = parse_openrouter_model(m)
            if not pm or not pm["created"]:
                continue

            sc = None
            if pm["id"] in aliases and aliases[pm["id"]] in by_slug:
                sc = by_slug[aliases[pm["id"]]]
                stats["alias"] += 1
            else:
                sc = match_scores(pm["id"], by_slug, by_norm, {})
                if sc:
                    short = pm["id"].split("/", 1)[-1] if "/" in pm["id"] else pm["id"]
                    if short in by_slug:
                        stats["slug"] += 1
                    else:
                        stats["norm"] += 1
                else:
                    stats["miss"] += 1

            if sc:
                pm["intelligence_score"] = sc["intelligence"]
                pm["coding_score"] = sc["coding"]

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

    print(f"      总模型 {len(models)}，Intelligence 覆盖 {with_intel}，Coding 覆盖 {with_coding}")
    print(f"      匹配分布: alias={stats['alias']}, slug={stats['slug']}, "
          f"norm={stats['norm']}, miss={stats['miss']}")

    print("[4/4] 写入 data/models.json…")
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "openrouter + artificial-analysis",
        "aa_models": len(aa_scores),
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