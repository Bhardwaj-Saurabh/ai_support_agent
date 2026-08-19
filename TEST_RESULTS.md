# Deployed Agent — Test Results

Agent ARN: `arn:aws:bedrock-agentcore:us-east-1:314182259346:runtime/csai_agent-D414VsAXhF`
Model: `global.amazon.nova-2-lite-v1:0` · Region: us-east-1
All tests run via `agentcore invoke` against the deployed AgentCore Runtime.

## Test 1 — Order Tracking
```
$ agentcore invoke '{"prompt": "Can you track order ORD-001?", "customer_id": "CUST-500", "session_id": "t1"}'
Response:
The order **ORD-001** for customer **CUST-123** is currently **SHIPPED**. Here 
are the details:

- **Item**: Wireless Headphones Pro (1 x $89.99)
- **Total**: $89.99
- **Carrier**: UPS
- **Tracking Number**: TRK987654321
- **Estimated Delivery**: August 21, 2026

Would you like me to help with anything else regarding this order?
```

## Test 2 — Refund Processing
```
$ agentcore invoke '{"prompt": "I want to return my Kindle Paperwhite (ORD-002). Please initiate a refund; reason: no longer needed.", ...}'
Response:
Your refund for the Kindle Paperwhite (Order #ORD-002) has been approved!

**Refund Summary:**
- **Refund ID:** REF-4GMYNMXF
- **Amount:** $139.99
- **Status:** Approved
- **Processing Time:** Credit will appear in your account within 3-5 business days

No further action is required on your part. The refund is already being processed.
```

## Test 3 — Knowledge Base (RAG)
```
$ agentcore invoke '{"prompt": "What are the benefits of the Platinum loyalty tier?", ...}'
Response:
The Platinum loyalty tier offers these key benefits:

- **Free same-day shipping** on eligible orders
- **15% discount** across most products
- **Priority customer support** for faster issue resolution
- **Higher point earning rates** (5 points per $1 spent on Amazon Fresh items)

To reach Platinum status, you need **5,000 or more loyalty points**. Points 
never expire, and you can redeem them for discounts (100 points = $1 off, with a
minimum redemption of 500 points).
```

## Test 5 — Loyalty Discount Calculation (Code Interpreter)
```
$ agentcore invoke '{"prompt": "I am a Gold member with 4250 points. Calculate my discount on a $150 standard order.", ...}'
Response:
Here's your loyalty discount breakdown for the $150 standard order:

**Your Gold Member Benefits:**
- **Tier discount:** 10% ($15)
- **Points redemption:** 4,000 points ($40 value)

**Final Pricing:**
- **Original order total:** $150.00
- **Total savings:** $51.00
- **Final amount due:** $99.00

**Points Summary:**
- **Points redeemed:** 4,000
- **Points earned from this purchase:** 99 
- **Remaining points balance:** 349

Your Gold membership saved you $51 on this order!
```

## Test 6 — Browser Tool
```
$ agentcore invoke '{"prompt": "Go to https://example.com and tell me the page title.", ...}'
Response:
The page title of https://example.com is **"Example Domain"**.
```

## Test 4 — Long-Term Memory (two sessions)
```
$ agentcore invoke '{"prompt": "Hi, I am Jane. I prefer concise responses.", "customer_id": "CUST-902", "session_id": "s-A"}'
Response:
Hello Jane! I can help you with order tracking, returns, refunds, and loyalty 
discounts. Please share what you'd like assistance with today.

# ... wait ~90s for memory extraction ...
$ agentcore invoke '{"prompt": "Do you remember my name and communication preference?", "customer_id": "CUST-902", "session_id": "s-B"}'
Response:
Yes, Jane. I remember you prefer concise responses.
```

