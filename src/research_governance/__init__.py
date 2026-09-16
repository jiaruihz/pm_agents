"""Shared contracts for reproducible research and durable knowledge handoff."""

from .record import ResearchRecord, build_prompt, load_record, validate_record_file

__all__ = ["ResearchRecord", "build_prompt", "load_record", "validate_record_file"]
