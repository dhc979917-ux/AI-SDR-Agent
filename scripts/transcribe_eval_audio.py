"""Transcribe an audio directory into a local evaluation draft (not gold)."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sdr.audio import AudioService
from sdr.auth import Auth
from sdr.config import ROOT, Settings
from sdr.model_settings import ModelSettingsService
from sdr.store import Store
from sdr.models import UserCreate
from sdr.workspace import new_customer


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio files into an evaluation draft")
    parser.add_argument("--source", default="data/录音测试集")
    parser.add_argument("--db", default="var/eval.sqlite3")
    parser.add_argument("--model-user", default="admin")
    parser.add_argument("--output", default="eval/audio_transcripts.json")
    args = parser.parse_args()
    source = ROOT / args.source
    files = sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() in (".mp3", ".wav", ".m4a", ".flac", ".ogg"))
    if not files:
        parser.error("没有找到音频文件")
    real_settings = Settings()
    real_store = Store(real_settings.db_path)
    model_settings = ModelSettingsService(real_store, real_settings)
    resolved = model_settings.resolve(args.model_user)
    if not resolved.asr_key:
        parser.error("模型用户没有配置 ASR key")
    db_path = ROOT / args.db
    db_path.unlink(missing_ok=True)
    store = Store(db_path)
    auth = Auth(store, resolved)
    actor = "eval_runner"
    auth.create_user(UserCreate(username=actor, name="Eval Runner", password="eval-only-password-123", role="admin"))
    with store.connection() as db:
        customer = new_customer(db, "评测客户", actor, actor)
    audio = AudioService(store, resolved)
    rows = []
    try:
        for index, path in enumerate(files, 1):
            print(f"[{index}/{len(files)}] {path.name}", flush=True)
            row, _ = audio.create(path.read_bytes(), path.name, customer["id"], "synthetic",
                                  f"audio-eval-{index}", actor, customer_mode="existing", auto_analyze=False)
            audio.run(row["id"])
            row = audio.get(row["id"], actor)
            role = row.get("role_result") or {}
            transcript = row.get("confirmed_text") or (role.get("transcript") if row.get("role_status") == "ready" else row.get("raw_text"))
            rows.append({
                "source_file": path.name,
                "status": row["status"],
                "role_status": row["role_status"],
                "transcript": transcript or "",
                "raw_text": row.get("raw_text") or "",
                "error": row.get("error"),
                "role_error": row.get("role_error"),
            })
    finally:
        audio.pool.shutdown(wait=True)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print(json.dumps({"output": str(output), "count": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
