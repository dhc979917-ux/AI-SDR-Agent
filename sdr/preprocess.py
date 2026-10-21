import re
from .models import Turn

LABEL = re.compile(r"^\s*(销售|客户|SDR|顾客|销售人员|客服|用户|其他|存疑|待确认)\s*[:：]\s*(.*)$", re.I)


def split_turns(text: str) -> list[Turn]:
    """Preserve utterances, repetitions and speakers; never rewrite customer text."""
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = LABEL.match(line)
        if match:
            speaker = {"其他":"other", "存疑":"excluded", "待确认":"pending"}.get(match[1]) or ("customer" if match[1] in ("客户", "顾客", "用户") else "sales")
            rows.append((speaker, match[2].strip()))
        elif rows:
            speaker, old = rows[-1]
            rows[-1] = (speaker, old + "\n" + line.strip())
        else:
            rows.append(("unknown", line.strip()))
    return [Turn(id=f"T{i:02}", speaker=s, text=t) for i, (s, t) in enumerate(rows, 1)]


def redact_for_model(turns: list[Turn]):
    """Redact actual phone numbers, preserving reversible per-request evidence mapping."""
    substitutions = {}
    safe = []
    for turn in turns:
        def replace(m):
            key = f"[PHONE_{len(substitutions) + 1}]"
            substitutions[key] = m[0]
            return key
        text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", replace, turn.text)
        safe.append({**turn.model_dump(), "text": text})
    return safe, substitutions


def restore_placeholders(value, substitutions):
    if isinstance(value, str):
        for key, original in substitutions.items():
            value = value.replace(key, original)
        return value
    if isinstance(value, list):
        return [restore_placeholders(v, substitutions) for v in value]
    if isinstance(value, dict):
        return {k: restore_placeholders(v, substitutions) for k, v in value.items()}
    return value
