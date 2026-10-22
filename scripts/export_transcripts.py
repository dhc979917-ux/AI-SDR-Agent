"""Export confirmed transcripts from an evaluation SQLite DB as labeling drafts."""
from pathlib import Path
import argparse
import json
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description="Export confirmed transcripts as labeled_cases drafts")
    parser.add_argument("--db", required=True, help="评测实例的 SQLite 路径，例如 var/eval.sqlite3")
    parser.add_argument("--output", default="eval/labeled_cases.draft.json")
    parser.add_argument("--prefix", default="R")
    args = parser.parse_args()
    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    try:
        rows = list(db.execute("""SELECT filename, confirmed_text FROM transcriptions
            WHERE confirmed_text IS NOT NULL AND confirmed_text != '' ORDER BY created_at, id"""))
    finally:
        db.close()
    if not rows:
        print("没有已确认的转写；请先在录音转写页保存确认稿。", file=sys.stderr)
        return 1
    cases = []
    for index, row in enumerate(rows, 1):
        cases.append({
            "id": f"{args.prefix}{index:02d}",
            "split": "test",
            "transcript": row["confirmed_text"],
            "notes": "draft：请根据录音人工补全企业可接受主体、意向、字段和拒联标记",
            "gold": {
                "company_accept": [],
                "intent_level": None,
                "do_not_contact": False,
                "fields": {
                    "industry": None, "area": None, "contact_name": None, "contact_title": None,
                    "phone": None, "mendian": None, "shejishi": None
                }
            }
        })
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(cases, ensure_ascii=False, indent=2))
    print(json.dumps({"output": str(output), "count": len(cases)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
