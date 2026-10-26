import logging
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ConfigDict, Field, model_validator

load_dotenv(Path(__file__).with_name(".env"))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
SYSTEM_INSTRUCTIONS = """You are Morrow, a thoughtful, capable general-purpose AI assistant.
Help with the user's actual question across topics: explain clearly, reason carefully,
write and brainstorm creatively, and adapt your level of detail to what they need.
Be warm, direct, and well organized. For complex requests, break the answer into
useful steps. Ask a brief clarifying question when essential context is missing.
Be honest about uncertainty; never invent facts, sources, or capabilities. Avoid
unnecessary disclaimers and filler. Use plain language and readable formatting."""


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def require_latest_user_message(self):
        if self.messages[-1].role != "user":
            raise ValueError("The latest message must be from the user.")
        return self


class ChatResponse(BaseModel):
    answer: str


app = FastAPI(title="Morrow Chat API", version="1.0.0")

configured_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in configured_origins.split(",")
        if origin.strip()
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "ai_configured": bool(os.getenv("OPENAI_API_KEY")),
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="The assistant is not configured yet. Add OPENAI_API_KEY to backend/.env.",
        )

    client = AsyncOpenAI(
        api_key=api_key,
        timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "45")),
        max_retries=1,
    )

    try:
        response = await client.responses.create(
            model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
            instructions=SYSTEM_INSTRUCTIONS,
            input=[
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
        )
    except OpenAIError as error:
        logger.exception("OpenAI request failed.")
        raise HTTPException(
            status_code=502,
            detail="The AI service could not complete your request. Please try again.",
        ) from error

    answer = response.output_text.strip()
    if not answer:
        logger.error("OpenAI returned an empty response.")
        raise HTTPException(
            status_code=502,
            detail="The AI service returned an empty response. Please try again.",
        )

    return ChatResponse(answer=answer)
