"""Document parsing and tenant-scoped local/Pinecone retrieval."""

import math
import re
from collections import Counter
from uuid import UUID

from database.models import BusinessChunk, BusinessDocument
from rag import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    MAX_EXTRACTED_CHARS,
    CVChunk,
    _pinecone_index,
    extract_cv_chunks,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool


def parse_document(filename: str, content: bytes) -> list[CVChunk]:
    if filename.lower().endswith((".txt", ".md")):
        try:
            text = content.decode("utf-8-sig").strip()
        except UnicodeDecodeError as error:
            raise ValueError("Text documents must use UTF-8 encoding.") from error
        if not text or len(text) > MAX_EXTRACTED_CHARS:
            raise ValueError(
                "Upload nonempty text containing at most 100,000 characters."
            )
        return [
            CVChunk(text=text[start : start + CHUNK_SIZE], section="Document")
            for start in range(0, len(text), CHUNK_SIZE - CHUNK_OVERLAP)
        ]
    return extract_cv_chunks(filename, content)


def namespace(workspace_id: UUID) -> str:
    return f"support-workspace-{workspace_id.hex}"


def index_document(
    workspace_id: UUID, document_id: UUID, chunks: list[CVChunk]
) -> None:
    index = _pinecone_index()
    for start in range(0, len(chunks), 100):
        index.documents.upsert(
            namespace=namespace(workspace_id),
            documents=[
                {
                    "_id": f"{document_id}-{start + offset}",
                    "text": chunk.text,
                    "document_id": str(document_id),
                    "chunk_index": start + offset,
                    "section": chunk.section,
                }
                for offset, chunk in enumerate(chunks[start : start + 100])
            ],
        )


def delete_indexed_document(workspace_id: UUID, document_id: UUID) -> None:
    _pinecone_index().documents.delete(
        namespace=namespace(workspace_id),
        filter={"document_id": {"$eq": str(document_id)}},
    )


def _search_index(workspace_id: UUID, ids: list[str], query: str):
    return (
        _pinecone_index()
        .documents.search(
            namespace=namespace(workspace_id),
            top_k=5,
            score_by=[{"type": "embed", "fields": ["text"], "query": query}],
            filter={"document_id": {"$in": ids}},
            include_fields=["document_id", "chunk_index"],
        )
        .matches
    )


STOP_WORDS = set(
    "a an the is are was were what how can do does did of to for in on at and or my your our me i we you please policy ticket customer request".split()
)


def terms(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9]+", text.lower())
        if word not in STOP_WORDS and len(word) > 1
    }


async def retrieve(
    db: AsyncSession, workspace_id: UUID, query: str, mode: str
) -> list[dict]:
    rows = (
        await db.execute(
            select(BusinessChunk, BusinessDocument.filename)
            .join(BusinessDocument, BusinessChunk.document_id == BusinessDocument.id)
            .where(
                BusinessChunk.workspace_id == workspace_id,
                BusinessDocument.workspace_id == workspace_id,
                BusinessDocument.status == "ready",
            )
            .order_by(BusinessChunk.document_id, BusinessChunk.position)
        )
    ).all()
    if not rows:
        return []
    lookup = {
        (str(chunk.document_id), chunk.position): (chunk, name) for chunk, name in rows
    }
    if mode == "gemini":
        hits = await run_in_threadpool(
            _search_index, workspace_id, list({key[0] for key in lookup}), query
        )
        selected = []
        for hit in hits:
            try:
                key = (str(hit.get("document_id", "")), int(hit.get("chunk_index", -1)))
            except (ValueError, TypeError):
                continue
            # Read source text from the authorized DB rows, never trust a remote hit
            # to introduce a different tenant/document or fabricated source content.
            if key in lookup and key not in selected:
                selected.append(key)
    else:
        query_terms = terms(query)
        expanded = set(query_terms)
        if query_terms & {"refund", "refunds", "return", "returns"}:
            expanded.update({"refund", "refunds", "return", "returns"})
        if query_terms & {"shipping", "shipped", "delivery", "tracking"}:
            expanded.update({"shipping", "shipped", "delivery", "tracking"})
        frequencies = Counter(word for chunk, _ in rows for word in terms(chunk.text))
        ranked = []
        for key, (chunk, _) in lookup.items():
            overlap = expanded & terms(chunk.text)
            score = sum(math.log(1 + len(rows) / frequencies[word]) for word in overlap)
            if score:
                ranked.append((score, key))
        selected = [key for _, key in sorted(ranked, reverse=True)[:5]]
    return [
        {
            "id": number,
            "document_id": key[0],
            "filename": lookup[key][1],
            "section": lookup[key][0].section,
            "position": key[1],
            "text": lookup[key][0].text,
        }
        for number, key in enumerate(selected, start=1)
    ]
