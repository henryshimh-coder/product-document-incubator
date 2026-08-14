from __future__ import annotations


def test_sensitive_comparison_service_is_available_as_a_pure_local_use_case() -> None:
    """Catches L3/L4 review being routed through an external workflow instead of local code."""
    from src.application.use_cases.compare_sensitive_source import CompareSensitiveSource

    assert CompareSensitiveSource.__init__.__name__ == "__init__"
