"""Bản chép luật chống prompt injection từ dự án ViGuard (C:\VISUAL\ViGuard), chỉ phần luật – không gọi LLM."""
from .rules import normalized

__all__ = ["normalized"]
