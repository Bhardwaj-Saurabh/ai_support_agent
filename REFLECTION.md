# Project Reflection

## A design decision
The most consequential decision was how to authenticate the agent to the
AgentCore Gateway. The standard path provisions an Amazon Cognito user pool with
a resource server and a machine-to-machine OAuth client, and the agent presents a
bearer token. I instead created the Gateway with the **`AWS_IAM` authorizer** and
signed each MCP request with **SigV4** using a small `httpx.Auth` shim. This
removed an entire moving part: there are no client secrets to store or rotate,
and the deployed runtime authenticates with its own execution-role identity, which
is exactly the credential AgentCore already gives it. It is both simpler and,
arguably, more secure than a long-lived OAuth secret.

## A challenge
The environment fought back. The lab account's `voclabs` role explicitly denied
`cognito-idp:CreateResourceServer`, so the Gateway's default OAuth quick-start
could never complete — in the console or the CLI, in every allowed region. On top
of that, the supplied credentials rotated between two accounts with different
permission sets, so early infrastructure landed in an account that later turned
out to lack AgentCore access entirely. Diagnosing this meant probing each service
directly and realizing the block was a *credential/permission* boundary, not a
region or a mistake in my setup. The `AWS_IAM` authorizer was the escape hatch
that turned a hard blocker into a one-line configuration change.

## A production consideration
The runtime execution role I attached uses `bedrock-agentcore:*` for speed. In
production this must be scoped to least privilege — specific memory, gateway,
code-interpreter, and browser resource ARNs. Two other realities matter for real
users: long-term memory is **eventually consistent** (extraction took 90–180
seconds), so the UX cannot assume a fact is recallable immediately after it is
stated; and the knowledge-base tool must **fail closed** — I wrapped it so a
retrieval error returns "unavailable" rather than letting the model answer from
imagination, which is essential when grounded accuracy is the whole point of RAG.
