"""Spintax simples: `{a|b|c}` vira uma opção sorteada, com aninhamento.

Usado para a bio em lote não ficar idêntica em todas as contas.
"""
from __future__ import annotations

import random
import re

MAX_DEPTH = 8


class SpintaxError(ValueError):
    pass


def has_spintax(text: str | None) -> bool:
    return bool(text) and "{" in text and "|" in text and "}" in text


def validate(text: str | None) -> None:
    """Levanta SpintaxError se as chaves não fecharem direito."""
    depth = 0
    for ch in text or "":
        if ch == "{":
            depth += 1
            if depth > MAX_DEPTH:
                raise SpintaxError("Muitos níveis de { } aninhados.")
        elif ch == "}":
            depth -= 1
            if depth < 0:
                raise SpintaxError("Tem um } sem { correspondente.")
    if depth != 0:
        raise SpintaxError("Faltou fechar } em alguma opção.")


def _split_options(body: str) -> list[str]:
    """Divide por | ignorando os | que estão dentro de chaves aninhadas."""
    options: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "{":
            depth += 1
            current.append(ch)
        elif ch == "}":
            depth -= 1
            current.append(ch)
        elif ch == "|" and depth == 0:
            options.append("".join(current))
            current = []
        else:
            current.append(ch)
    options.append("".join(current))
    return options


def _spin_once(text: str, rng: random.Random) -> str:
    """Resolve o bloco {…} mais interno (sem { dentro) uma vez."""
    match = re.search(r"\{([^{}]*)\}", text)
    if not match:
        return text
    options = _split_options(match.group(1))
    choice = rng.choice(options) if options else ""
    return text[: match.start()] + choice + text[match.end() :]


def spin(text: str | None, rng: random.Random | None = None) -> str:
    """Sorteia uma variação. Texto sem spintax volta igual."""
    if not text:
        return ""
    validate(text)
    rng = rng or random
    out = text
    for _ in range(200):
        new = _spin_once(out, rng)
        if new == out:
            break
        out = new
    return out


def preview(text: str | None, count: int = 3) -> list[str]:
    rng = random.Random()
    return [spin(text, rng) for _ in range(max(1, count))]
