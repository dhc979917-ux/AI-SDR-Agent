from dataclasses import dataclass, field, replace
from pathlib import Path
import json
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)


@dataclass
class Settings:
    db_path: Path = field(default_factory=lambda: Path(os.getenv("SDR_DB_PATH", str(ROOT / "var/sdr.sqlite3"))))
    api_key: str = field(default_factory=lambda: os.getenv("SDR_LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY", ""))
    base_url: str = field(default_factory=lambda: os.getenv("SDR_LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    model: str = field(default_factory=lambda: os.getenv("SDR_LLM_MODEL", "qwen-plus"))
    timeout: float = 45.0
    entity_url: str = field(default_factory=lambda: os.getenv("SDR_ENTITY_API_URL", ""))
    entity_key: str = field(default_factory=lambda: os.getenv("SDR_ENTITY_API_KEY", ""))
    entity_catalog: Path = field(default_factory=lambda: Path(os.getenv("SDR_ENTITY_CATALOG", str(ROOT / "data/entities.json"))))
    session_hours: int = 12
    cookie_secure: bool = field(default_factory=lambda: os.getenv('SDR_COOKIE_SECURE', 'false').lower() == 'true')
    asr_key: str = field(default_factory=lambda: os.getenv('SDR_ASR_API_KEY') or os.getenv('DASHSCOPE_API_KEY', ''))
    asr_base_url: str = field(default_factory=lambda: os.getenv('SDR_ASR_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1'))
    asr_model: str = field(default_factory=lambda: os.getenv('SDR_ASR_MODEL', 'qwen3-asr-flash'))
    asr_protocol: str = field(default_factory=lambda: os.getenv('SDR_ASR_PROTOCOL', 'qwen_asr'))
    text_provider: str = 'environment'
    audio_provider: str = 'environment'
    asr_timeout: float = 120.0
    audio_max_bytes: int = 200 * 1024 * 1024
    audio_max_seconds: int = 3600
    audio_quota_bytes: int = 2 * 1024 * 1024 * 1024
    asr_segment_seconds: int = 180
    audio_decode_timeout: float = 180.0
    auto_publish: bool = field(default_factory=lambda: os.getenv("SDR_AUTO_PUBLISH", "false").lower() == "true")
    max_pending: int = 20

    def resolved(self,actor=None):
        manager=getattr(self,'model_settings',None)
        return manager.resolve(actor) if manager and actor else replace(self)

    def public(self):
        return {"llm_available": bool(self.api_key), "model": self.model,
                "entity_provider": "external" if self.entity_url else "local_catalog",
                "auth_required": True, "auto_publish": self.auto_publish,
                "asr_available": bool(self.asr_key), "asr_model": self.asr_model,
                "text_provider":self.text_provider,"audio_provider":self.audio_provider,
                "audio_max_bytes": self.audio_max_bytes, "audio_max_seconds": self.audio_max_seconds,
                "asr_segment_seconds": self.asr_segment_seconds,
                "transcript_max_chars": 100000,
                "local_demo": False, "version": "0.2.0"}
