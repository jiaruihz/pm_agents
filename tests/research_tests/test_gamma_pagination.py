import pytest

from src.domains.research.clients.gamma import fetch_paginated


class DummyClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = 0

    async def get_json(self, endpoint, params=None):
        offset = params.get("offset", 0)
        self.calls += 1
        page_idx = offset // params.get("limit")
        return self.pages.get(page_idx, [])

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_fetch_paginated_collects_pages(monkeypatch):
    pages = {
        0: [{"id": 1}, {"id": 2}],
        1: [{"id": 3}],
    }

    dummy = DummyClient(pages)

    def _client_factory():
        return dummy

    monkeypatch.setattr("src.domains.research.clients.gamma.HttpClient", _client_factory)
    data = await fetch_paginated("http://example/markets", params={"limit": 2}, max_pages=None)
    assert len(data) == 3
    assert dummy.calls == 2
