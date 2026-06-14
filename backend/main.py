from typing import Dict, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.agent import run_refund_agent
from backend.data import init_db, list_orders
from backend.policy import POLICY_TEXT
from backend.traces import read_traces, clear_traces
from backend.llm import llm_is_configured, DEFAULT_MODEL

app = FastAPI(title="RefundGuard AI API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/config")
def config() -> Dict[str, Any]:
    return {"llm_configured": llm_is_configured(), "llm_provider": "openai", "model": DEFAULT_MODEL, "agent_orchestration": "LangGraph"}


@app.post("/chat")
def chat(request: ChatRequest) -> Dict[str, Any]:
    return run_refund_agent(request.message)


@app.get("/traces")
def traces() -> Dict[str, Any]:
    return {"traces": read_traces()}


@app.delete("/traces")
def reset_traces() -> Dict[str, str]:
    clear_traces()
    return {"status": "cleared"}


@app.get("/orders")
def orders() -> Dict[str, Any]:
    return {"orders": list_orders()}


@app.get("/policy")
def policy() -> Dict[str, str]:
    return {"policy": POLICY_TEXT}
