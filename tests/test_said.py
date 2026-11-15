"""The UI says Crossfade's own English server sentences in the person's language (web/src/lib/ui/said.ts).
Every sentence it knows must still be one the server says, or the translation quietly stops working."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAID = ROOT / "web/src/lib/ui/said.ts"


def _server_text() -> str:
    src = "\n".join(p.read_text() for p in (ROOT / "crossfade").rglob("*.py"))
    # "first part " \n "second part" -> one sentence (strings continued on the next line)
    return re.sub(r"\"\s*\n\s*f?\"", "", src)


def _rules() -> list[tuple[str, str]]:
    rules = re.findall(r"^\t\[(.+), '(said\.[\w.]+)'\],?$", SAID.read_text(), re.M)
    assert len(rules) >= 20
    return rules


def _fragments(pattern: str) -> list[str]:
    if pattern.startswith("new RegExp(`"):
        body = pattern[len("new RegExp(`"):pattern.rindex("`")].replace("${SVC}", "\0").replace("\\\\", "\\")
    else:
        body = pattern[pattern.index("/") + 1:pattern.rindex("/")]
    body = re.sub(r"\(\?<\w+>[^)]*\)", "\0", body).replace("^", "").replace("$", "")
    body = re.sub(r"\\(.)", r"\1", body)
    return [f for f in re.split(r"[\0()|?*+]", body) if len(f.strip()) >= 6]


def test_every_translated_sentence_is_still_said_by_the_server():
    server = _server_text()
    for pattern, key in _rules():
        parts = _fragments(pattern)
        assert parts, key
        for part in parts:
            assert part in server, f"{key}: “{part}” is no longer said by the server"


def test_every_translation_exists_in_both_languages():
    keys = {k for _, k in _rules()}
    for lang in ("de", "en"):
        text = (ROOT / f"web/src/lib/ui/lang/{lang}.ts").read_text()
        missing = [k for k in keys if f"'{k}':" not in text]
        assert not missing, (lang, missing)
