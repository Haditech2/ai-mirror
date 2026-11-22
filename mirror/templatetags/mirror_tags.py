from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def format_bytes(value: int | None) -> str:
    size = float(value or 0)
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.1f} {units[index]}" if index else f"{int(size)} B"


@register.filter
def short_id(value: object, length: int = 12) -> str:
    text = str(value)
    return f"{text[:length]}…" if len(text) > length else text


@register.filter
def status_dot(value: str) -> str:
    if value == "completed":
        return "bg-emerald-400"
    if value in {"failed", "deleted"}:
        return "bg-rose-400"
    return "bg-amber-300"
