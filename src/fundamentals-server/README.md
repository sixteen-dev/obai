# Fundamentals MCP Server

MCP server for company fundamental analysis with AI-enhanced educational context from financial PDFs.

## Features

- **Financial Statements**: Income statement, balance sheet, cash flow with FMP API
- **Company Profile**: Sector, industry, description, key executives
- **Key Metrics**: P/E ratio, ROE, ROIC, revenue per share
- **Financial Ratios**: Liquidity, profitability, efficiency ratios
- **SEC Filings**: 10-K, 10-Q, 8-K and other regulatory filings with direct links
- **Insider Trading**: Corporate insider transactions (executives, directors, 10%+ shareholders)
- **Revenue Segments**: Business line breakdown by product category
- **Educational Context**: AI-powered search across 19 financial PDFs (options, ETFs, bonds, etc.)
- **Structured Logging**: JSON logs with structlog for observability
- **Security Auditing**: Tool invocation tracking, API call logging, error tracking

## Tools

### `get_fundamentals`
Get financial statements with optional educational context from PDFs.

```json
{
  "symbol": "AAPL",
  "statement_type": "income",
  "period": "annual",
  "limit": 5,
  "include_context": true
}
```

### `get_company_profile`
Get company overview with optional industry context.

```json
{
  "symbol": "AAPL",
  "include_context": false
}
```

### `search_fundamentals`
Search educational content from financial PDFs.

```json
{
  "query": "What are option strategies for volatility?",
  "top_k": 5
}
```

### `get_key_metrics`
Get key financial metrics.

```json
{
  "symbol": "AAPL",
  "period": "annual",
  "limit": 5
}
```

### `get_financial_ratios`
Get financial ratios.

```json
{
  "symbol": "AAPL",
  "period": "quarter",
  "limit": 8
}
```

### `get_sec_filings`
Get SEC filings (10-K, 10-Q, 8-K, etc.) for deep fundamental research. Dates default to last 3 months if not specified.

```json
{
  "symbol": "AAPL",
  "limit": 10,
  "from_date": "2024-01-01",
  "to_date": "2024-12-31"
}
```

### `get_insider_trades`
Get insider trading activity for sentiment analysis.

```json
{
  "symbol": "AAPL",
  "limit": 20
}
```

### `get_revenue_segments`
Get revenue breakdown by product/business segment.

```json
{
  "symbol": "AAPL",
  "period": "annual"
}
```

## Setup

### AWS Secrets Manager Configuration

All sensitive configuration is loaded from **AWS Secrets Manager** at boot time.

#### 1. Create Main Secrets

```bash
# Create secret with API keys
aws secretsmanager create-secret \
  --name fundamentals-server/secrets \
  --secret-string '{
    "FMP_API_KEY": "your_fmp_api_key",
    "OPENAI_API_KEY": "your_openai_api_key"
  }' \
  --region us-east-2 \
  --profile teznewz-dev
```

#### 2. (Optional) Create JWT Secret for Authentication

```bash
# Create JWT secret (minimum 32 characters)
aws secretsmanager create-secret \
  --name fundamentals-server/jwt-secret \
  --secret-string '{
    "jwt_secret": "your-super-secret-jwt-key-min-32-chars"
  }' \
  --region us-east-2 \
  --profile teznewz-dev
```

### Local Development

#### Using Docker Compose (Recommended)

1. **Copy environment template:**
```bash
cd dev
cp .env.example .env
```

2. **Edit `dev/.env`:**
```bash
AWS_REGION=us-east-2
SECRETS_ID=fundamentals-server/secrets
JWT_SECRET_ID=fundamentals-server/jwt-secret  # Optional
```

3. **Start server:**
```bash
docker-compose up --build
```

The server will:
- Load API keys from `fundamentals-server/secrets`
- Load JWT secret from `fundamentals-server/jwt-secret` (if `JWT_SECRET_ID` is set)
- Run on http://localhost:8000

#### Using Python Directly

1. **Install dependencies:**
```bash
cd src/fundamentals-server
uv sync --all-extras --dev
```

2. **Set environment variables:**
```bash
export AWS_REGION=us-east-2
export SECRETS_ID=fundamentals-server/secrets
export JWT_SECRET_ID=fundamentals-server/jwt-secret  # Optional
```

3. **Run component tests:**
```bash
uv run python test_local.py
```

4. **Start server:**
```bash
uv run python -m src.server
```

### Testing with MCP Inspector

Use the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) to interactively test your MCP server running in Docker:

#### 1. Build and run Docker container

```bash
cd dev
docker-compose up --build
```

The server will be available at `http://localhost:8000`

#### 2. Open MCP Inspector

```bash
npx @modelcontextprotocol/inspector
```

#### 3. Configure connection

In the MCP Inspector UI:
- **Connection Type:** Streamable HTTP
- **Server URL:** `http://localhost:8000/mcp`

Or use the command-line transport if testing the stdio interface:
- **Command:** `docker`
- **Arguments:** `["run", "--rm", "-e", "AWS_REGION=us-east-2", "-e", "SECRETS_ID=fundamentals-server/secrets", "-v", "$HOME/.aws:/root/.aws:ro", "fundamentals-server"]`

#### 4. Test tools interactively

- View all 11 available tools in the inspector UI
- Test `get_fundamentals` with different symbols (AAPL, MSFT, GOOGL)
- Try `search_fundamentals` with queries like "What are option strategies?"
- Test `get_company_profile` with `include_context=true`
- Try `get_sec_filings` with `filing_type="10-K"` for annual reports
- Test `get_insider_trades` to see executive transactions
- Use `get_revenue_segments` to see product line breakdowns
- Inspect tool schemas, parameters, and responses
- Debug authentication if JWT is enabled

#### Inspector Benefits:
- ✅ **Real-time testing** - Test tools without writing code
- ✅ **Request/response inspection** - See exactly what's happening
- ✅ **Schema validation** - Verify tool parameters are correct
- ✅ **Error debugging** - Catch issues before production
- ✅ **Docker testing** - Test the actual production image

## Authentication

JWT authentication is **optional** and loaded from AWS Secrets Manager at startup.

### Enable Authentication

Auth is disabled by default. To enable it, set `ENABLE_JWT=true` and provide a JWT secret id.

Set `JWT_SECRET_ID` environment variable pointing to your Secrets Manager secret:

```bash
export JWT_SECRET_ID=fundamentals-server/jwt-secret
```

### Generate Client Tokens

Use the provided token generator:

```python
from src.auth import generate_client_token

# Load secret from Secrets Manager first
secret_key = await get_jwt_secret_from_secrets("fundamentals-server/jwt-secret")

# Generate token for Discord bot
token = generate_client_token(
    secret_key,
    client_id="discord-bot",
    hours=168  # 7 days
)
```

### Use Token in MCP Client

```python
# Configure your MCP client with the token
mcp_client = MCPClient(
    url="http://fundamentals-server:8000",
    headers={"Authorization": f"Bearer {token}"}
)
```

### Disable Authentication

By default, authentication is disabled. Ensure `ENABLE_JWT` is not set (or set to `false`).

## Production Deployment (ECS/Fargate)

### IAM Permissions

Ensure your ECS task role has permission to read secrets:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": [
        "arn:aws:secretsmanager:us-east-2:*:secret:fundamentals-server/secrets*",
        "arn:aws:secretsmanager:us-east-2:*:secret:fundamentals-server/jwt-secret*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3vectors:QueryVectors"
      ],
      "Resource": "arn:aws:s3vectors:us-east-2:*:bucket/tezq-financial-vectors/*"
    }
  ]
}
```

### Environment Variables for ECS

```bash
AWS_REGION=us-east-2
SECRETS_ID=fundamentals-server/secrets
JWT_SECRET_ID=fundamentals-server/jwt-secret  # Optional
```

### Build and Deploy

```bash
# Build Docker image
docker build -t fundamentals-server .

# Tag and push to ECR
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-2.amazonaws.com
docker tag fundamentals-server:latest <account>.dkr.ecr.us-east-2.amazonaws.com/fundamentals-server:latest
docker push <account>.dkr.ecr.us-east-2.amazonaws.com/fundamentals-server:latest
```

## Vector Search

Uses S3 Vectors bucket: `tezq-financial-vectors/financial-fundamentals`

**Processed PDFs:**
- Options guides (OIC, strategies)
- Financial ratios cheat sheet
- ETFs and mutual funds
- Bond basics
- Asset allocation
- Short selling
- Futures trading
- And more...

## Logging

All logs are structured JSON (via structlog) and include:
- **Tool invocations**: Track all MCP tool calls with parameters
- **API calls**: FMP, OpenAI, S3 Vectors with sanitized params
- **Authentication events**: JWT verification, token generation
- **Errors**: Full context with stack traces
- **Security auditing**: Sensitive data automatically redacted

Example log output:
```json
{
  "event": "tool_invocation",
  "tool_name": "get_fundamentals",
  "symbol": "AAPL",
  "statement_type": "income",
  "timestamp": "2025-01-05T10:30:45.123Z",
  "level": "info"
}
```

## Development

```bash
# Lint
uv run ruff check . --fix

# Format
uv run ruff format .

# Type check (strict mode)
uv run mypy src/ --strict

# Run all checks
uv run ruff check . && uv run ruff format . && uv run mypy src/ --strict
```

## Project Structure

```
fundamentals-server/
├── src/
│   ├── server.py           # FastMCP server entry point
│   ├── config.py           # Pydantic settings + Secrets Manager
│   ├── secrets.py          # AWS Secrets Manager integration
│   ├── auth.py             # JWT authentication
│   ├── logging_config.py   # Structured logging setup
│   ├── audit.py            # Security auditing functions
│   ├── clients/
│   │   ├── fmp_client.py         # FMP API client
│   │   └── s3_vectors_client.py  # S3 Vectors client
│   └── tools/
│       ├── fundamentals.py       # Financial analysis tools
│       └── vector_search.py      # Educational content search
├── dev/
│   ├── docker-compose.yml  # Local development
│   └── .env.example        # Environment template
├── Dockerfile              # Production container
├── pyproject.toml          # Dependencies + config
├── test_local.py           # Local testing script
└── README.md
├── VERSION                 # Auto-bumped by pre-commit hook
├── VERSION_MANAGEMENT.md   # Version management docs
```

### Version Management

Version is automatically bumped on every commit via pre-commit hook:
- Version stored in `VERSION` file
- Auto-increments patch version (e.g., `0.1.0` → `0.1.1`)
- Updates `pyproject.toml` automatically
- See `VERSION_MANAGEMENT.md` for details
## Amazon Bedrock AgentCore Integration

Amazon Bedrock AgentCore expects MCP servers to:
- Serve Streamable HTTP at path `/mcp`
- Run stateless Streamable HTTP (AgentCore provides session isolation and `Mcp-Session-Id` header)

This server is configured accordingly by default:
- Transport: `streamable-http`
- Path: `/mcp`
- Stateless: enabled

Health check remains available at `GET /health`.
