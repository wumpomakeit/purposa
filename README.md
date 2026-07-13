# Purposa

**Multi-Agent DAO Governance Analysis & Voting Assistant**

Built for the **OKX AI Genesis Hackathon — Agent Service Provider (ASP) track**.

---

## What is Purposa?

Purposa collapses the DAO governance workflow into one flow:

**understand the proposal → get a multi-agent verdict → vote**

Without leaving your conversation context.

### The problem it solves

- Governance proposals are long, technical, and buried in forum context
- Most token holders skip voting (voter apathy) or vote based on influencer takes
- Delegates waste time manually reading and cross-checking proposals
- Even informed voters face friction switching to Snapshot to actually vote

### How it works

```
1. Agent/user → POST /analyze with a Snapshot proposal URL
2. HTTP 402 returned → x402 payment challenge (OKX exact scheme)
3. onchainos payment pay --payload '<PAYMENT-REQUIRED header value>'
4. Replay request with Authorization: <returned_header>
5. HTTP 200 → { summary, pros, cons, risk_flags, recommendation, confidence, trace_link }
6. User reviews verdict
7. User confirms → POST /vote → Purposa signs and submits vote via OKX Agentic Wallet
```

---

## Quick Start

### 1. Prerequisites

```bash
# Install onchainos CLI
curl -sSL https://raw.githubusercontent.com/okx/onchainos-skills/main/install.sh | sh
source ~/.bashrc   # or open a new terminal
onchainos --version
```

### 2. Credentials

Copy the example env file and fill in your keys:

```bash
cp .env.example .env
# Edit .env with your credentials (never commit .env to git)
```

Required in `.env`:

```bash
# From https://web3.okx.com/onchainos/dev-portal
OKX_API_KEY="your-api-key"
OKX_SECRET_KEY="your-secret-key"
OKX_PASSPHRASE="your-passphrase"

# At least one LLM provider
OPENAI_API_KEY="sk-..."
# ANTHROPIC_API_KEY="sk-ant-..."
```

### 3. Set up OKX Agentic Wallet

```bash
bash scripts/setup_okx.sh   # auto-reads .env and configures onchainos

# If not already logged in to the CLI wallet:
onchainos wallet login
# Enter your email → enter OTP code → wallet created with TEE-protected key
```

### 4. Install Python dependencies

```bash
pip install -e ".[dev]"
```

### 5. Run the service

```bash
python3 -m src.main
# or: uvicorn src.main:app --reload
```

API docs: http://localhost:8000/docs

---

## API Reference

### `GET /health`

Check service status, wallet connection, and credential availability.

```json
{
  "status": "ok",
  "version": "0.1.0",
  "wallet_connected": true,
  "okx_credentials": true,
  "llm_available": true,
  "onchainos_version": "onchainos 4.2.4"
}
```

### `POST /analyze` *(pay-per-call, x402)*

Analyze a Snapshot governance proposal.

**Request:**
```json
{
  "proposal_url": "https://snapshot.org/#/uniswap.eth/proposal/0xabc..."
}
```

**Without payment header → HTTP 402:**
```json
{
  "error": "Payment Required",
  "x402_payload": "<base64>",
  "instructions": "onchainos payment pay --payload '<x402_payload>'"
}
```

**Pay and replay:**
```bash
# Step 1: Get payment challenge
PAYLOAD=$(curl -s -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"proposal_url":"<url>"}' | jq -r '.x402_payload')

# Step 2: Pay via OKX Agentic Wallet
AUTH=$(onchainos payment pay --payload "$PAYLOAD" | jq -r '.authorization_header')

# Step 3: Replay with auth header
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -H "Authorization: $AUTH" \
  -d '{"proposal_url":"<url>"}'
```

**Response (HTTP 200):**
```json
{
  "trace_id": "uuid",
  "proposal_id": "0xabc...",
  "proposal_title": "Upgrade Treasury Multisig",
  "tldr": "Proposal to migrate treasury to a new 5-of-9 multisig with updated signers.",
  "summary": "...",
  "pros": [{ "point": "Increases signer diversity", "strength": "high" }],
  "cons": [{ "point": "Migration involves 48h lockup", "strength": "medium" }],
  "risk_flags": [{ "category": "process", "severity": "low", "flag": "Short discussion period" }],
  "overall_risk_level": "low",
  "recommendation": "For",
  "recommended_choice_index": 0,
  "recommended_choice_label": "Yes",
  "confidence": 0.82,
  "reasoning": "...",
  "key_considerations": ["Signer diversity", "Treasury security", "Migration risk"],
  "dissenting_view": "Some may prefer status quo multisig",
  "warnings": [],
  "choices": ["Yes", "No", "Abstain"],
  "vote_state": "active",
  "agent_trace": [...],
  "elapsed_ms": 4521
}
```

### `POST /vote`

Submit a vote via OKX Agentic Wallet (explicit confirmation required).

```json
{
  "proposal_url": "https://snapshot.org/#/uniswap.eth/proposal/0xabc...",
  "choice_index": 0,
  "reason": "Agreed with Purposa's analysis",
  "trace_id": "uuid-from-analyze"
}
```

The vote is EIP-712 signed inside the OKX TEE — private key never leaves the enclave.

### `GET /trace/{trace_id}`

Retrieve the full reasoning trace for a prior analysis (audit trail).

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Purposa                              │
│                                                              │
│  POST /analyze                                               │
│     │                                                        │
│     ├─ x402 payment gate (OKX exact scheme)                  │
│     │                                                        │
│     └─ Multi-Agent Pipeline ──────────────────────┐         │
│           │                                        │         │
│           ├── Summarizer Agent (LLM 1)             │         │
│           ├── Critic Agent     (LLM 2)             │ async   │
│           └── Risk Assessor    (LLM 1)             │         │
│                    │                               │         │
│                    └── Judge Agent (LLM judge) ←──┘         │
│                              │                               │
│                         Verdict + Trace                      │
│                                                              │
│  POST /vote                                                  │
│     └─ onchainos wallet sign-message (EIP-712)               │
│         └─ Submit to Snapshot Hub API                        │
└─────────────────────────────────────────────────────────────┘
         ↑                          ↑
   OKX Agentic Wallet         Snapshot GraphQL
   (TEE-protected key)        (public, no key)
```

### Components

| Component | Technology |
|---|---|
| HTTP service | FastAPI (Python) |
| Multi-agent pipeline | OpenAI / Anthropic (configurable) |
| Proposal data | Snapshot GraphQL API |
| Payments | x402 exact scheme, OKX onchainos CLI |
| Wallet & signing | OKX Agentic Wallet via onchainos CLI |
| Vote submission | Snapshot Hub REST API (EIP-712) |
| Trace storage | Local JSON files (configurable) |

---

## Multi-Agent Design

The analysis runs three specialist agents in parallel, then a judge reconciles them:

1. **Summarizer** — neutral plain-language summary, TL;DR, timeline
2. **Critic** — independent pros/cons with strength ratings
3. **Risk Assessor** — flags treasury risk, process risk, quorum risk, etc.
4. **Judge** — reads all three outputs, reconciles disagreements, produces a recommendation with confidence score

This ensemble pattern is more reliable than a single-prompt summarizer: each agent has a narrower mandate, and the judge can reason about where agents agree vs disagree.

---

## Payment Configuration

Set in `.env`:

```bash
ANALYSIS_PRICE_USDT=100        # 100 = 0.0001 USD₮0 (testnet; raise for production)
PAYMENT_NETWORK=eip155:1952    # X Layer Testnet (use eip155:196 for mainnet)
PAYMENT_TOKEN_ADDRESS=0x9e29b3aada05bf2d2c827af80bd28dc0b9b4fb0c
SELLER_ADDRESS=0x...           # auto-set by setup_okx.sh
```

In development (no `SELLER_ADDRESS`), payment verification is bypassed so you can test without a funded wallet.

---

## Testing

```bash
python3 -m pytest tests/ -v
```

---

## Docker

```bash
docker compose up --build
```

---

## Roadmap

- **v1 (MVP)**: Snapshot proposals, x402 payment, multi-agent verdict, EIP-712 vote
- **v2**: On-chain governance (Tally, Compound Governor Bravo), batch session payments
- **v3**: Persistent delegate profiles, historical voting pattern analysis, multi-DAO subscriptions

---

## License

MIT
