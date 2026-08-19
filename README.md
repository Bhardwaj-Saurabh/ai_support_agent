# Customer Support AI Agent — Amazon Bedrock AgentCore + Strands

An intelligent e-commerce customer support agent that handles order tracking,
returns/refunds, product & policy Q&A, cross-session memory, exact loyalty-discount
math, and live web browsing — all through one conversational interface, deployed
on **Amazon Bedrock AgentCore Runtime** using the **Strands SDK**.

## Capabilities

| Capability | How it's implemented |
|---|---|
| Order tracking & customer lookup | Lambda (`order-tracker`) behind API Gateway, exposed via **AgentCore Gateway** (MCP) |
| Refunds, refund status, return labels | Lambda (`refund-processor`) as a direct Lambda **Gateway** target |
| Product / policy Q&A (RAG) | `search_knowledge_base` → **Bedrock Knowledge Base** Retrieve API |
| Cross-session memory | `MemoryHook` → **AgentCore Memory** (semantic facts + user preferences) |
| Exact loyalty discounts | `calculate_loyalty_discount` → **AgentCore Code Interpreter** sandbox |
| Live web browsing | **AgentCore Browser** tool |

Model: `global.amazon.nova-2-lite-v1:0` · Region: `us-east-1`

## Architecture notes

- **Gateway auth uses `AWS_IAM` (SigV4), not Cognito OAuth.** The MCP connection is
  signed with the caller's AWS credentials via the `SigV4HTTPXAuth` shim in
  [main.py](main.py). This avoids OAuth client-secret management and uses the
  runtime's execution-role identity directly.
- Memory is managed in application code (the `MemoryHook`), so the AgentCore
  deployment itself is configured with memory disabled.

## Layout

```
main.py                    # the agent — all 8 implementation sections complete
lambda/order_tracker.py    # deployed to AWS Lambda (order/customer lookup)
lambda/refund_processor.py # deployed to AWS Lambda (refunds)
lambda/lambda_schema       # tool schema for the refund Gateway target
product_catalog.txt        # Knowledge Base source (uploaded to S3)
requirements.txt           # runtime container dependencies
.bedrock_agentcore.yaml    # AgentCore deployment config
TEST_RESULTS.md            # output of all six test scenarios (deployed)
REFLECTION.md              # written reflection
```

## Run locally

```bash
uv sync
uv run main.py '{"prompt": "Can you track order ORD-001?", "customer_id": "CUST-123", "session_id": "s1"}'
```

## Deploy & invoke

```bash
agentcore configure --entrypoint main.py --name csai_agent \
  --execution-role <runtime-role-arn> --requirements-file requirements.txt \
  --s3 <deploy-bucket> --region us-east-1 --disable-otel --disable-memory
agentcore deploy
agentcore invoke '{"prompt": "What are the benefits of the Platinum loyalty tier?", "customer_id": "CUST-123", "session_id": "t3"}'
```

## Configuration values (in `main.py`)

| Var | Value |
|---|---|
| `GATEWAY_URL` | `https://customersupportgateway-j1o6wwgb1e.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp` |
| `KB_ID` | `LNAJSTJHYI` |
| `MEMORY_ID` | `CustomerSupportMemory-HUHjiQ57Zd` |
| `REGION` | `us-east-1` |
