import asyncio

from app.services.async_sse import sse_from_sync_iterator


def test_sse_from_sync_iterator_emits_json_events():
    async def collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in sse_from_sync_iterator(iter([{"type": "stage"}, {"type": "done"}])):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect())
    assert len(chunks) == 2
    assert '"type": "stage"' in chunks[0]
    assert chunks[0].startswith("data: ")
    assert chunks[0].endswith("\n\n")
