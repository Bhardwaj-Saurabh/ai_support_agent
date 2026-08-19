"""
Customer Support AI Agent
=========================
A multi-capability customer support agent built on Amazon Bedrock AgentCore
and the Strands SDK. It combines:

  • Order tracking & refunds  — Lambda tools via the AgentCore Gateway (MCP)
  • Product / policy Q&A       — RAG over a Bedrock Knowledge Base
  • Cross-session memory       — AgentCore Memory (semantic facts + preferences)
  • Loyalty discount math      — AgentCore Code Interpreter (secure sandbox)
  • Live web browsing          — AgentCore Browser tool

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

NOTE ON GATEWAY AUTH
--------------------
This gateway was created with the AWS_IAM authorizer (SigV4), not Cognito OAuth.
The MCP connection is therefore signed with the caller's AWS credentials via a
small httpx auth shim (SigV4HTTPXAuth) rather than a bearer token.
"""

# ── Imports ───────────────────────────────────────────────────────────────────
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client
import argparse, json
import os, asyncio, boto3
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
)
import logging
import uuid
from typing import Dict
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands_tools.browser import AgentCoreBrowser

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("CSAI_Agent")

# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
app = BedrockAgentCoreApp()


# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
GATEWAY_URL = "https://customersupportgateway-j1o6wwgb1e.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
KB_ID       = os.environ.get("KB_ID", "LNAJSTJHYI")
REGION      = "us-east-1"
MEMORY_ID   = "CustomerSupportMemory-HUHjiQ57Zd"


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
model_id = "global.amazon.nova-2-lite-v1:0"

model = BedrockModel(model_id=model_id, region_name=REGION)
memory_client = MemoryClient(region_name=REGION)
_bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)


# ── Gateway auth shim (AWS_IAM / SigV4) ───────────────────────────────────────
class SigV4HTTPXAuth(httpx.Auth):
    """Sign each outgoing HTTP request to the AgentCore Gateway with SigV4."""

    requires_request_body = True

    def __init__(self, service: str, region: str):
        self._service = service
        self._region = region
        self._credentials = boto3.Session().get_credentials()

    def auth_flow(self, request):
        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=dict(request.headers),
        )
        SigV4Auth(self._credentials, self._service, self._region).add_auth(aws_request)
        request.headers.update(dict(aws_request.headers))
        yield request


# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    strategies = mem_client.get_memory_strategies(memory_id)
    return {s["type"]: s["namespaces"][0] for s in strategies}


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_client = memory_client
        self.memory_id = memory_id
        self.namespaces = get_namespaces(memory_client, memory_id)

    @staticmethod
    def _extract_text(memory: dict) -> str:
        """Pull the plain-text body out of a retrieved memory record."""
        content = memory.get("content", memory)
        if isinstance(content, dict):
            return (content.get("text") or "").strip()
        if isinstance(content, str):
            return content.strip()
        return ""

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        messages = event.agent.messages
        if not messages:
            return
        last = messages[-1]
        if last.get("role") != "user":
            return
        content = last.get("content", [])
        # Skip tool results — only enrich genuine plain-text user turns.
        if any(isinstance(c, dict) and "toolResult" in c for c in content):
            return
        text_blocks = [c["text"] for c in content if isinstance(c, dict) and "text" in c]
        if not text_blocks:
            return
        user_query = text_blocks[0]

        gathered = []
        for strategy_type, ns_template in self.namespaces.items():
            namespace = ns_template.format(actorId=self.actor_id)
            try:
                memories = self.memory_client.retrieve_memories(
                    memory_id=self.memory_id,
                    namespace=namespace,
                    query=user_query,
                    top_k=5,
                )
            except Exception as e:
                logger.warning("Memory retrieve failed for %s: %s", namespace, e)
                continue
            for m in memories or []:
                text = self._extract_text(m)
                if text:
                    gathered.append(f"[{strategy_type}] {text}")

        if gathered:
            context_block = "\n".join(gathered)
            last["content"] = [
                {"text": f"Customer Context:\n{context_block}\n\n{user_query}"}
            ]

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        messages = event.agent.messages
        if not messages:
            return

        customer_query = None
        agent_response = None
        # Walk backwards: last assistant text, then the preceding user text.
        for message in reversed(messages):
            role = message.get("role")
            content = message.get("content", [])
            if any(isinstance(c, dict) and "toolResult" in c for c in content):
                continue
            texts = [c["text"] for c in content if isinstance(c, dict) and "text" in c]
            if not texts:
                continue
            if role == "assistant" and agent_response is None:
                agent_response = texts[0]
            elif role == "user" and customer_query is None:
                customer_query = texts[0]
            if customer_query and agent_response:
                break

        if not (customer_query and agent_response):
            return

        try:
            self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=self.actor_id,
                session_id=self.session_id,
                messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")],
            )
        except Exception as e:
            logger.warning("Memory create_event failed: %s", e)

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)


# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    if not KB_ID:
        return "Knowledge base not configured."

    try:
        resp = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
        )
    except Exception as e:
        # Never let the model answer from imagination on a KB failure.
        logger.warning("Knowledge base retrieve failed: %s", e)
        return (
            "The knowledge base is currently unavailable, so I can't retrieve "
            "grounded information for this question right now. Please try again shortly."
        )

    results = resp.get("retrievalResults", [])
    if not results:
        return "No relevant information found in the knowledge base."

    chunks = [r.get("content", {}).get("text", "") for r in results]
    chunks = [c for c in chunks if c]
    return "\n---\n".join(chunks)


# ── TODO 7 — Loyalty Discount Tool (Code Interpreter) ────────────────────────
@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """
    code = f"""
import json, math

loyalty_points = {int(loyalty_points)}
tier = {tier!r}
order_total = {float(order_total)}
product_category = {product_category!r}

earn_rates = {{"standard": 1, "device": 2, "fresh": 5}}
tier_rates = {{"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}}

# 100 points = $1. Redeem in blocks of 500 points, capped at 50% of the order.
max_points_by_order = int((order_total * 0.5) * 100)
redeemable = min(loyalty_points, max_points_by_order)
points_redeemed = (redeemable // 500) * 500
points_value = points_redeemed / 100.0

subtotal_after_points = max(order_total - points_value, 0.0)

tier_rate = tier_rates.get(tier, 0.0)
tier_discount = round(subtotal_after_points * tier_rate, 2)

final_total = round(subtotal_after_points - tier_discount, 2)
total_savings = round(points_value + tier_discount, 2)

earn_rate = earn_rates.get(product_category, 1)
points_earned = int(math.floor(final_total) * earn_rate)
remaining_points = loyalty_points - points_redeemed + points_earned

result = {{
    "order_total": round(order_total, 2),
    "tier": tier,
    "tier_discount_rate": tier_rate,
    "points_redeemed": points_redeemed,
    "points_value_usd": round(points_value, 2),
    "tier_discount_usd": tier_discount,
    "final_total": final_total,
    "total_savings": total_savings,
    "points_earned": points_earned,
    "remaining_points": remaining_points,
}}
print(json.dumps(result))
"""

    try:
        with code_session(REGION) as session:
            execution = session.invoke(
                "executeCode",
                {"language": "python", "code": code, "clearContext": True},
            )
            for event in execution["stream"]:
                return json.dumps(event["result"])
        return "Code Interpreter returned no result."

    except Exception as e:
        # Fallback: tier discount only, no sandbox.
        logger.warning("Code Interpreter unavailable, using fallback: %s", e)
        tier_rate = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}.get(tier, 0.0)
        tier_discount = round(order_total * tier_rate, 2)
        final_total = round(order_total - tier_discount, 2)
        return json.dumps({
            "order_total": round(order_total, 2),
            "tier": tier,
            "tier_discount_rate": tier_rate,
            "tier_discount_usd": tier_discount,
            "final_total": final_total,
            "note": "Approximate: computed without Code Interpreter (points not redeemed).",
        })


# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a helpful customer support assistant for an Amazon-style e-commerce platform.

You can:
- Track orders and look up customer profiles (order-tracker tools).
- Initiate refunds, check refund status, and generate return labels (refund-processor tools).
- Answer product, policy, warranty, and loyalty-program questions using search_knowledge_base.
- Calculate exact loyalty discounts with calculate_loyalty_discount.
- Browse live web pages with the browser tool when asked about external/live information.

Guidelines:
- Use the customer's ID from the conversation context when calling order/customer tools.
- Prefer search_knowledge_base for policy and product facts rather than guessing.
- Always use calculate_loyalty_discount for discount math — never compute it yourself.
- Be concise, accurate, and friendly. If a tool returns an error, explain it plainly.
"""


# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent
    """
    user_input = payload.get("prompt", "")
    actor_id = payload.get("customer_id", "anonymous")
    session_id = payload.get("session_id") or str(uuid.uuid4())

    if not user_input:
        return "Please provide a 'prompt' describing how I can help."

    # Long-term memory for this customer/session.
    memory_hook = MemoryHook(
        actor_id=actor_id,
        session_id=session_id,
        memory_client=memory_client,
        memory_id=MEMORY_ID,
    )

    # Live web browsing tool.
    agent_core_browser = AgentCoreBrowser(region=REGION)

    # Local tools; gateway tools are appended after the MCP connection opens.
    tools = [search_knowledge_base, calculate_loyalty_discount, agent_core_browser.browser]

    # Connect to the AgentCore Gateway over MCP (SigV4-signed / AWS_IAM auth).
    gateway_auth = SigV4HTTPXAuth("bedrock-agentcore", REGION)
    gateway_client = MCPClient(
        lambda: streamablehttp_client(GATEWAY_URL, auth=gateway_auth)
    )

    try:
        with gateway_client:
            gateway_tools = gateway_client.list_tools_sync()
            tools.extend(gateway_tools)

            agent = Agent(
                model=model,
                tools=tools,
                hooks=[memory_hook],
                system_prompt=SYSTEM_PROMPT,
            )

            # Give the model the customer context it needs for tool arguments.
            framed_input = f"[customer_id={actor_id}] {user_input}"
            result = await agent.invoke_async(framed_input)

        # Return the text of the first content block of the response.
        message = getattr(result, "message", None)
        if isinstance(message, dict):
            for block in message.get("content", []):
                if isinstance(block, dict) and "text" in block:
                    return block["text"]
        return str(result)

    except Exception as e:
        logger.exception("Agent invocation failed")
        return f"Sorry, I ran into an error handling that request: {e}"


# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    # app.run()
    # Uncomment app.run() (and comment main()) for AgentCore deployment.
    main()
