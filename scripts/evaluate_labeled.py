"""Evaluate a labeled call set: entity recall, intent confusion and field metrics."""
from pathlib import Path
from collections import Counter
import argparse
import hashlib
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sdr.config import ROOT, Settings
from sdr.engine import Engine
from sdr.models import AnalyzeRequest
from sdr.store import Store, dumps

LABELS = ("high", "medium", "low")
FIELD_NAMES = ("industry", "area", "contact_name", "contact_title", "phone", "mendian", "shejishi")


def _norm(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _pred_company(result):
    lead = result.get("lead") or {}
    entity = result.get("entity") or {}
    return lead.get("company_name") or (entity.get("selected") or {}).get("name")


def _pred_candidates(result):
    return [item.get("name") for item in (result.get("entity") or {}).get("candidates", []) if item.get("name")]


def _field(result, name):
    return ((result.get("extraction") or {}).get("fields") or {}).get(name, {}).get("value")


def _run(engine, store, case, mode):
    request = AnalyzeRequest(transcript=case["transcript"], title=case["id"], mode=mode,
                             data_mode="synthetic", idempotency_key="labeled-" + case["id"]).model_dump()
    fingerprint = hashlib.sha256(dumps(request).encode()).hexdigest()
    row, _ = store.create(request, fingerprint, "evaluation")
    return engine.run(row["id"])


def _entity_metrics(rows):
    cases = [row for row in rows if row["gold"].get("company_accept")]
    if not cases:
        return None
    top1 = sum(row["pred_company"] in row["accept"] for row in cases)
    top3 = sum(any(name in row["accept"] for name in row["pred_candidates"][:3]) for row in cases)
    recall = sum(any(name in row["accept"] for name in row["pred_candidates"]) for row in cases)
    auto = [row for row in cases if row["auto_confirmed"]]
    auto_correct = sum(row["pred_company"] in row["accept"] for row in auto)
    abstained = sum(row["entity_status"] in ("ambiguous", "unresolved", "lookup_failed") for row in cases)
    return {
        "count": len(cases),
        "top1_accuracy": round(top1 / len(cases), 4),
        "top3_recall": round(top3 / len(cases), 4),
        "candidate_recall": round(recall / len(cases), 4),
        "auto_confirmed_count": len(auto),
        "auto_confirm_precision": round(auto_correct / len(auto), 4) if auto else None,
        "false_confirmation_rate": round((len(auto) - auto_correct) / len(auto), 4) if auto else None,
        "abstention_rate": round(abstained / len(cases), 4),
    }


def _intent_metrics(rows):
    matrix = {gold: {pred: 0 for pred in LABELS + ("null",)} for gold in LABELS + ("null",)}
    for row in rows:
        gold = row["gold"].get("intent_level")
        pred = row["pred_intent"]
        matrix[gold if gold in LABELS else "null"][pred if pred in LABELS else "null"] += 1
    per_class = {}
    f1_values = []
    for label in LABELS:
        tp = matrix[label][label]
        fp = sum(matrix[gold][label] for gold in LABELS + ("null",) if gold != label)
        fn = sum(matrix[label][pred] for pred in LABELS + ("null",) if pred != label)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
        if f1 is not None:
            f1_values.append(f1)
        per_class[label] = {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}
    dnc_cases = [row for row in rows if row["gold"].get("do_not_contact")]
    dnc_recall = (sum(row["pred_dnc"] for row in dnc_cases) / len(dnc_cases)) if dnc_cases else None
    return {
        "count": len(rows),
        "confusion_matrix": matrix,
        "per_class": per_class,
        "macro_f1": round(sum(f1_values) / len(f1_values), 4) if f1_values else None,
        "do_not_contact_recall": round(dnc_recall, 4) if dnc_recall is not None else None,
        "do_not_contact_count": len(dnc_cases),
    }


def _field_metrics(rows):
    metrics = {}
    for name in FIELD_NAMES:
        gold_nonnull = pred_nonnull = precision_correct = recall_correct = 0
        for row in rows:
            gold_fields = row["gold"].get("fields") or {}
            if name not in gold_fields:
                continue
            gold = _norm(gold_fields[name])
            pred = _norm(row["pred_fields"].get(name))
            if gold is not None:
                gold_nonnull += 1
                if pred == gold:
                    recall_correct += 1
            if pred is not None:
                pred_nonnull += 1
                if pred == gold:
                    precision_correct += 1
        metrics[name] = {
            "gold_nonnull": gold_nonnull,
            "pred_nonnull": pred_nonnull,
            "correct": recall_correct,
            "precision": round(precision_correct / pred_nonnull, 4) if pred_nonnull else None,
            "recall": round(recall_correct / gold_nonnull, 4) if gold_nonnull else None,
            "coverage": round(pred_nonnull / gold_nonnull, 4) if gold_nonnull else None,
        }
    return metrics


def main():
    parser = argparse.ArgumentParser(description="Evaluate labeled calls")
    parser.add_argument("--dataset", default="eval/labeled_cases.json")
    parser.add_argument("--mode", choices=("rules", "llm"), default="rules")
    parser.add_argument("--entity-provider", choices=("auto", "qcc", "off", "local"), default="auto")
    parser.add_argument("--output", default="eval/artifacts/labeled-eval.json")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    path = ROOT / args.dataset
    if not path.exists():
        parser.error(f"找不到标注集：{path}")
    cases = json.loads(path.read_text())
    if any(case.get("draft") for case in cases):
        parser.error("标注文件包含 draft=true；请回放核对并删除 draft 标记后再评测")
    if args.limit:
        cases = cases[:args.limit]
    temp = Path(tempfile.mkdtemp(prefix="sdr-labeled-"))
    settings = Settings(db_path=temp / "eval.sqlite", entity_provider=args.entity_provider)
    if args.mode == "llm" and not settings.api_key:
        parser.error("LLM 模式需要配置 SDR_LLM_API_KEY")
    store = Store(settings.db_path)
    engine = Engine(store, settings)
    rows = []
    try:
        for case in cases:
            row = _run(engine, store, case, args.mode)
            result = row.get("result") or {}
            entity = result.get("entity") or {}
            gold = case.get("gold") or {}
            accept = gold.get("company_accept") or []
            rows.append({
                "id": case["id"],
                "status": row["status"],
                "gold": gold,
                "accept": accept,
                "pred_company": _pred_company(result),
                "pred_candidates": _pred_candidates(result),
                "entity_status": entity.get("status"),
                "auto_confirmed": bool(entity.get("auto_confirmed")),
                "pred_intent": (result.get("intent") or {}).get("level"),
                "pred_total": (result.get("intent") or {}).get("total"),
                "pred_dnc": bool((result.get("extraction") or {}).get("do_not_contact")),
                "pred_fields": {name: _field(result, name) for name in FIELD_NAMES},
                "field_mismatches": {
                    name: {"gold": _norm((gold.get("fields") or {}).get(name)),
                           "pred": _norm(_field(result, name))}
                    for name in FIELD_NAMES
                    if name in (gold.get("fields") or {})
                    and _norm((gold.get("fields") or {}).get(name)) != _norm(_field(result, name))
                },
            })
    finally:
        engine.pool.shutdown(wait=True)
    report = {
        "kind": "labeled_call_evaluation_not_synthetic_regression",
        "dataset": str(path.relative_to(ROOT)),
        "mode": args.mode,
        "entity_provider": args.entity_provider,
        "count": len(rows),
        "entity": _entity_metrics(rows),
        "intent": _intent_metrics(rows),
        "fields": _field_metrics(rows),
        "rows": rows,
        "limitations": "样本量较小时指标波动很大；报告应同时给出样本数、覆盖率和人工标注口径。",
    }
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({
        "count": report["count"],
        "entity_top1": (report["entity"] or {}).get("top1_accuracy"),
        "entity_top3": (report["entity"] or {}).get("top3_recall"),
        "entity_candidate_recall": (report["entity"] or {}).get("candidate_recall"),
        "intent_macro_f1": (report["intent"] or {}).get("macro_f1"),
        "dnc_recall": (report["intent"] or {}).get("do_not_contact_recall"),
    }, ensure_ascii=False))
    for row in rows:
        if row["status"] not in ("completed", "failed"):
            print("未完成", row["id"], row["status"])


if __name__ == "__main__":
    raise SystemExit(main())
