import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from backend.data import lookup_order
from backend.policy import check_policy
from backend.traces import save_trace
from backend.llm import generate_llm_customer_response


class RefundAgentState(TypedDict, total=False):
    """State passed between LangGraph nodes for one refund request."""

    trace_id: str
    start_time: float
    user_message: str
    order_id: Optional[str]
    order: Optional[Dict[str, Any]]
    policy_result: Optional[Dict[str, Any]]
    fallback_response: str
    final_response: str
    decision: str
    reason: str
    steps: List[Dict[str, Any]]
    llm_metadata: Dict[str, Any]
    latency_ms: int
    estimated_tokens: int
    estimated_cost_usd: float


def add_step(
    state: RefundAgentState,
    step_name: str,
    tool: str,
    tool_input: Any,
    tool_output: Any,
    status: str = "success",
) -> RefundAgentState:
    """Append one trace step. This is what the admin dashboard displays."""
    steps = list(state.get("steps", []))
    steps.append(
        {
            "step": len(steps) + 1,
            "step_name": step_name,
            "tool": tool,
            "input": tool_input,
            "output": tool_output,
            "status": status,
        }
    )
    return {"steps": steps}


def direct_extract_order_id(message: str) -> Optional[str]:
    match = re.search(r"\bORD-\d{4}\b", message.upper())
    return match.group(0) if match else None


def retry_extract_order_id(message: str) -> Optional[str]:
    """Retry-friendly extraction: 'ord 1001', 'order 1001', or 'ORD1001'."""
    loose = re.search(r"\b(?:ORD|ORDER)\s*-?\s*(\d{4})\b", message.upper())
    if loose:
        return f"ORD-{loose.group(1)}"
    return None


def extract_order_id_node(state: RefundAgentState) -> RefundAgentState:
    message = state["user_message"]

    order_id = direct_extract_order_id(message)
    update = add_step(
        state,
        "Extract order id",
        "direct_order_id_extractor",
        {"message": message},
        {"order_id": order_id},
        "success" if order_id else "failed",
    )
    state = {**state, **update}

    if order_id:
        return {"order_id": order_id, "steps": state["steps"]}

    retry_order_id = retry_extract_order_id(message)
    update = add_step(
        state,
        "Retry order id extraction",
        "normalized_order_id_extractor",
        {"message": message, "normalization": "supports ORD1001, ord 1001, order 1001"},
        {"order_id": retry_order_id},
        "success" if retry_order_id else "failed",
    )
    state = {**state, **update}

    if retry_order_id:
        return {"order_id": retry_order_id, "steps": state["steps"]}

    response = "I couldn’t find an order ID in your message. Please provide an order number like ORD-1001."
    return {
        "order_id": None,
        "decision": "NEEDS_MORE_INFO",
        "reason": "No recognizable order ID was provided.",
        "final_response": response,
        "steps": state["steps"],
    }


def lookup_order_node(state: RefundAgentState) -> RefundAgentState:
    order_id = state.get("order_id")
    order = lookup_order(order_id) if order_id else None

    update = add_step(
        state,
        "Lookup order in CRM",
        "lookup_order",
        {"order_id": order_id},
        sanitize_order(order),
        "success" if order else "failed",
    )
    state = {**state, **update}

    if not order:
        response = f"I couldn’t find order {order_id} in the CRM. Please check the order number and try again."
        return {
            "order": None,
            "decision": "ORDER_NOT_FOUND",
            "reason": "The order was not found in the mock CRM database.",
            "final_response": response,
            "steps": state["steps"],
        }

    return {"order": order, "steps": state["steps"]}


def policy_check_node(state: RefundAgentState) -> RefundAgentState:
    order = state["order"]
    user_message = state["user_message"]
    policy_result = check_policy(order, user_message)

    update = add_step(
        state,
        "Validate refund policy",
        "check_policy",
        {
            "order_id": state.get("order_id"),
            "amount": order["amount"],
            "final_sale": bool(order["final_sale"]),
            "product_type": order["product_type"],
            "message": user_message,
        },
        policy_result,
    )
    state = {**state, **update}

    return {
        "policy_result": policy_result,
        "decision": policy_result["decision"],
        "reason": policy_result["reason"],
        "steps": state["steps"],
    }


def fallback_response_node(state: RefundAgentState) -> RefundAgentState:
    policy_result = state["policy_result"]
    order = state["order"]
    fallback_response = build_customer_response(policy_result, order)

    update = add_step(
        state,
        "Build safe fallback response",
        "response_builder",
        {"decision": policy_result["decision"], "reason": policy_result["reason"]},
        {"response": fallback_response},
    )
    state = {**state, **update}

    return {"fallback_response": fallback_response, "steps": state["steps"]}


def llm_response_node(state: RefundAgentState) -> RefundAgentState:
    policy_result = state["policy_result"]
    order = state["order"]

    response, llm_metadata = generate_llm_customer_response(
        user_message=state["user_message"],
        order=order,
        policy_result=policy_result,
        fallback_response=state["fallback_response"],
    )

    update = add_step(
        state,
        "Generate customer response with LLM",
        "llm_response_generator",
        {
            "decision_locked_by_policy_engine": policy_result["decision"],
            "reason_locked_by_policy_engine": policy_result["reason"],
            "important_guardrail": "LLM can only word the response. It cannot change approve/deny/escalate.",
        },
        {"response": response, "llm_metadata": llm_metadata},
        "success" if llm_metadata.get("used_llm") else "fallback",
    )
    state = {**state, **update}

    return {"final_response": response, "llm_metadata": llm_metadata, "steps": state["steps"]}


def finalize_trace_node(state: RefundAgentState) -> RefundAgentState:
    latency_ms = int((time.time() - state["start_time"]) * 1000)
    response = state.get("final_response", "I’m sorry, I wasn’t able to complete the refund review.")
    steps = state.get("steps", [])
    full_text_for_estimate = state.get("user_message", "") + response + str(steps)
    llm_metadata = state.get("llm_metadata", {})
    token_count = llm_metadata.get("total_tokens") or estimate_tokens(full_text_for_estimate)
    cost = llm_metadata.get("estimated_cost_usd") if llm_metadata.get("used_llm") else estimate_cost(full_text_for_estimate)

    trace = {
        "trace_id": state["trace_id"],
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "orchestration": "LangGraph StateGraph",
        "user_message": state["user_message"],
        "decision": state.get("decision", "UNKNOWN"),
        "reason": state.get("reason", "No reason recorded."),
        "final_response": response,
        "order": sanitize_order(state.get("order")),
        "steps": steps,
        "llm": llm_metadata,
        "latency_ms": latency_ms,
        "estimated_tokens": token_count,
        "estimated_cost_usd": cost,
    }
    save_trace(trace)
    return trace


def route_after_extract(state: RefundAgentState) -> str:
    if state.get("decision") == "NEEDS_MORE_INFO":
        return "finalize"
    return "lookup_order"


def route_after_lookup(state: RefundAgentState) -> str:
    if state.get("decision") == "ORDER_NOT_FOUND":
        return "finalize"
    return "policy_check"


def build_graph():
    graph = StateGraph(RefundAgentState)

    graph.add_node("extract_order_id", extract_order_id_node)
    graph.add_node("lookup_order", lookup_order_node)
    graph.add_node("policy_check", policy_check_node)
    graph.add_node("fallback_response", fallback_response_node)
    graph.add_node("llm_response", llm_response_node)
    graph.add_node("finalize", finalize_trace_node)

    graph.set_entry_point("extract_order_id")
    graph.add_conditional_edges(
        "extract_order_id",
        route_after_extract,
        {"lookup_order": "lookup_order", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "lookup_order",
        route_after_lookup,
        {"policy_check": "policy_check", "finalize": "finalize"},
    )
    graph.add_edge("policy_check", "fallback_response")
    graph.add_edge("fallback_response", "llm_response")
    graph.add_edge("llm_response", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


_refund_graph = build_graph()


def run_refund_agent(user_message: str) -> Dict[str, Any]:
    """Public function used by FastAPI. Runs the LangGraph refund workflow."""
    initial_state: RefundAgentState = {
        "trace_id": str(uuid.uuid4())[:8],
        "start_time": time.time(),
        "user_message": user_message,
        "steps": [],
    }
    return _refund_graph.invoke(initial_state)


def build_customer_response(policy_result: Dict[str, Any], order: Dict[str, Any]) -> str:
    decision = policy_result["decision"]
    reason = policy_result["reason"]

    if decision == "APPROVED":
        return (
            f"Good news — your refund for order {order['order_id']} has been approved. "
            f"Reason: {reason}"
        )

    if decision == "DENIED":
        return (
            f"I understand this may be frustrating, but I’m unable to process a refund for order {order['order_id']}. "
            f"Reason: {reason}"
        )

    return (
        f"Your refund request for order {order['order_id']} needs human review before a final decision. "
        f"Reason: {reason} A support specialist will review it next."
    )


def sanitize_order(order: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not order:
        return None
    return {
        "order_id": order["order_id"],
        "customer_id": order["customer_id"],
        "customer_name": order["name"],
        "product_name": order["product_name"],
        "amount": order["amount"],
        "purchase_date": order["purchase_date"],
        "final_sale": bool(order["final_sale"]),
        "product_type": order["product_type"],
        "suspicious_refund_history": bool(order["suspicious_refund_history"]),
    }


def estimate_tokens(text: str) -> int:
    # Rough local estimate used when provider usage metadata is unavailable.
    return max(1, len(text) // 4)


def estimate_cost(text: str) -> float:
    # Rough mock cost for demo fallback. Real LLM calls use provider usage metadata in backend/llm.py.
    tokens = estimate_tokens(text)
    return round((tokens / 1000) * 0.005, 6)
