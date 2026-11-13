"""Company document parsing and workspace-scoped pgvector/keyword retrieval."""

import math
import os
import re
from collections import Counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai.embeddings import embed_texts
from database.models import BusinessChunk, BusinessDocument, Workspace
from resolveai.documents import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    MAX_EXTRACTED_CHARS,
    DocumentChunk,
    extract_document_chunks,
)


def parse_document(filename: str, content: bytes) -> list[DocumentChunk]:
    if not filename.lower().endswith((".txt", ".md")):
        return extract_document_chunks(filename, content)
    try:
        text = content.decode("utf-8-sig").strip()
    except UnicodeDecodeError as error:
        raise ValueError("Text documents must use UTF-8 encoding.") from error
    if not text or len(text) > MAX_EXTRACTED_CHARS:
        raise ValueError(
            f"Upload nonempty text containing at most {MAX_EXTRACTED_CHARS:,} characters."
        )
    groups, paragraphs, path = [], [], []
    for line in text.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            if paragraphs:
                groups.append((" / ".join(path) or "Document", "\n".join(paragraphs)))
            level = len(heading[1])
            path = path[: level - 1] + [heading[2]]
            paragraphs = [line]
        else:
            paragraphs.append(line)
    if paragraphs:
        groups.append((" / ".join(path) or "Document", "\n".join(paragraphs)))
    return [
        DocumentChunk(
            text=body[start : start + CHUNK_SIZE].strip(), section=section[:160]
        )
        for section, body in groups
        if body.strip()
        for start in range(0, len(body), CHUNK_SIZE - CHUNK_OVERLAP)
        if body[start : start + CHUNK_SIZE].strip()
    ]


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
    conditions = (
        BusinessChunk.workspace_id == workspace_id,
        BusinessDocument.workspace_id == workspace_id,
        BusinessDocument.status == "ready",
    )
    if mode == "gemini":
        if not await db.scalar(
            select(BusinessChunk.id)
            .join(BusinessDocument, BusinessChunk.document_id == BusinessDocument.id)
            .where(*conditions, BusinessChunk.embedding.is_not(None))
            .limit(1)
        ):
            return []
        workspace = await db.get(Workspace, workspace_id)
        vector = (await embed_texts([query], workspace.embedding_model, query=True))[0]
        distance = BusinessChunk.embedding.cosine_distance(vector)
        threshold = max(0.0, min(1.0, float(os.getenv("RAG_MIN_SIMILARITY", "0.35"))))
        candidates = (
            await db.execute(
                select(
                    BusinessChunk, BusinessDocument.filename, distance.label("distance")
                )
                .join(
                    BusinessDocument, BusinessChunk.document_id == BusinessDocument.id
                )
                .where(*conditions, BusinessChunk.embedding.is_not(None))
                .order_by(distance, BusinessChunk.id)
                .limit(8)
            )
        ).all()
        selected = [
            (chunk, name, 1 - float(score))
            for chunk, name, score in candidates
            if 1 - float(score) >= threshold
        ][:5]
    else:
        rows = (
            await db.execute(
                select(BusinessChunk, BusinessDocument.filename)
                .join(
                    BusinessDocument, BusinessChunk.document_id == BusinessDocument.id
                )
                .where(*conditions)
                .order_by(BusinessChunk.document_id, BusinessChunk.position)
            )
        ).all()
        expanded = terms(query)
        for synonyms in (
            {"refund", "refunds", "return", "returns"},
            {"shipping", "shipped", "delivery", "tracking"},
        ):
            if expanded & synonyms:
                expanded.update(synonyms)
        frequencies = Counter(word for chunk, _ in rows for word in terms(chunk.text))
        ranked = []
        for chunk, name in rows:
            overlap = expanded & terms(chunk.text)
            score = sum(math.log(1 + len(rows) / frequencies[word]) for word in overlap)
            if score:
                ranked.append((score, chunk.position, chunk, name))
        selected = [
            (chunk, name, None)
            for _, _, chunk, name in sorted(
                ranked, key=lambda x: (x[0], x[1]), reverse=True
            )[:5]
        ]
    return [
        {
            "id": number,
            "document_id": str(chunk.document_id),
            "filename": name,
            "section": chunk.section,
            "position": chunk.position,
            "text": chunk.text,
            "score": score,
        }
        for number, (chunk, name, score) in enumerate(selected, 1)
    ]
