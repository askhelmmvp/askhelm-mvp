"""Shared pytest fixtures for the AskHelm test suite."""
import pytest


@pytest.fixture(autouse=True)
def clear_compliance_cache():
    """Clear the in-process compliance response cache before and after every test
    so cached answers from one test do not bleed into another."""
    from domain import compliance_engine
    compliance_engine._compliance_cache.clear()
    yield
    compliance_engine._compliance_cache.clear()
