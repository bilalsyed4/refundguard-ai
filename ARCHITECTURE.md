# Architecture

```text
Streamlit UI
  |
  | POST /chat
  v
FastAPI Backend
  |
  v
LangGraph StateGraph
  |
  |-- extract_order_id
  |-- lookup_order
  |-- policy_check
  |-- fallback_response
  |-- llm_response
  |-- finalize_trace
  v
SQLite CRM + Refund Policy + JSON Trace Logs
```

## Agent Flow

The agent flow is intentionally policy-first:

1. Customer sends a refund request.
2. LangGraph extracts or normalizes the order ID.
3. The backend looks up order and customer data in SQLite.
4. Deterministic policy rules decide APPROVED, DENIED, or ESCALATED.
5. A safe fallback response is built.
6. The LLM optionally rewrites the response, but cannot change the locked policy decision.
7. A trace is saved for the admin dashboard.

## Prompt Injection Handling

The policy engine is the source of truth. Customer messages like “ignore the policy” or “I am your manager” are treated as user input only and cannot override policy results.
