import re
import time
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

from backend.data import lookup_order
from backend.policy import check_policy
from backend.traces import save_trace
from backend.llm import generate_llm_customer_response


def extract_order_id(message: str) -> Optional[str]:
    """Extract order id like ORD-1001. Supports retry cleanup like 'ord 1001'."""
    direct = re.search(r"\bORD-\d{4}\b", message.upper())
    if direct:
        return direct.group(0)

    # Retry-friendly extraction: ord 1001, order 1001, ORD1001
    loose = re.search(r"\b(?:ORD|ORDER)\s*-?\s*(\d{4})\b", message.upper())
    if loose:
        return f"ORD-{loose.group(1)}"

    return None


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


def run_refund_agent(user_message: str) -> Dict[str, Any]:
    start = time.time()
    trace_id = str(uuid.uuid4())[:8]
    steps = []

    def log_step(step_name: str, tool: str, tool_input: Any, tool_output: Any, status: str = "success") -> None:
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

    # Step 1: Extract order id
    order_id = extract_order_id(user_message)
    log_step(
        "Extract order id",
        "extract_order_id",
        {"message": user_message},
        {"order_id": order_id},
        "success" if order_id else "failed",
    )

    if not order_id:
        latency_ms = int((time.time() - start) * 1000)
        response = "I couldn’t find an order ID in your message. Please provide an order number like ORD-1001."
        trace = {
            "trace_id": trace_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "user_message": user_message,
            "decision": "NEEDS_MORE_INFO",
            "final_response": response,
            "steps": steps,
            "latency_ms": latency_ms,
            "estimated_tokens": estimate_tokens(user_message + response),
            "estimated_cost_usd": estimate_cost(user_message + response),
        }
        save_trace(trace)
        return trace

    # Step 2: Lookup order
    order = lookup_order(order_id)
    log_step(
        "Lookup order in CRM",
        "lookup_order",
        {"order_id": order_id},
        sanitize_order(order),
        "success" if order else "failed",
    )

    if not order:
        latency_ms = int((time.time() - start) * 1000)
        response = f"I couldn’t find order {order_id} in the CRM. Please check the order number and try again."
        trace = {
            "trace_id": trace_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "user_message": user_message,
            "decision": "ORDER_NOT_FOUND",
            "final_response": response,
            "steps": steps,
            "latency_ms": latency_ms,
            "estimated_tokens": estimate_tokens(user_message + response),
            "estimated_cost_usd": estimate_cost(user_message + response),
        }
        save_trace(trace)
        return trace

    # Step 3: Check policy
    policy_result = check_policy(order, user_message)
    log_step(
        "Validate refund policy",
        "check_policy",
        {"order_id": order_id, "amount": order["amount"], "final_sale": bool(order["final_sale"]), "message": user_message},
        policy_result,
    )

    # Step 4: Build deterministic fallback response
    fallback_response = build_customer_response(policy_result, order)
    log_step(
        "Build safe fallback response",
        "response_builder",
        {"decision": policy_result["decision"], "reason": policy_result["reason"]},
        {"response": fallback_response},
    )

    # Step 5: Use LLM only for natural wording, not for the refund decision
    response, llm_metadata = generate_llm_customer_response(
        user_message=user_message,
        order=order,
        policy_result=policy_result,
        fallback_response=fallback_response,
    )
    log_step(
        "Generate customer response with LLM",
        "llm_response_generator",
        {
            "decision_locked_by_policy_engine": policy_result["decision"],
            "reason_locked_by_policy_engine": policy_result["reason"],
        },
        {"response": response, "llm_metadata": llm_metadata},
        "success" if llm_metadata.get("used_llm") else "fallback",
    )

    latency_ms = int((time.time() - start) * 1000)
    full_text_for_estimate = user_message + response + str(steps)
    token_count = llm_metadata.get("total_tokens") or estimate_tokens(full_text_for_estimate)
    cost = llm_metadata.get("estimated_cost_usd") if llm_metadata.get("used_llm") else estimate_cost(full_text_for_estimate)

    trace = {
        "trace_id": trace_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "user_message": user_message,
        "decision": policy_result["decision"],
        "reason": policy_result["reason"],
        "final_response": response,
        "order": sanitize_order(order),
        "steps": steps,
        "llm": llm_metadata,
        "latency_ms": latency_ms,
        "estimated_tokens": token_count,
        "estimated_cost_usd": cost,
    }
    save_trace(trace)
    return trace


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
    # Rough local estimate. In Phase 2, replace with actual provider usage metadata.
    return max(1, len(text) // 4)


def estimate_cost(text: str) -> float:
    # Rough mock cost for demo. In Phase 2, replace with real model cost calculation.
    tokens = estimate_tokens(text)
    return round((tokens / 1000) * 0.005, 6)
