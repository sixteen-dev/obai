# OBaI Testing Guide

Complete guide for testing the OBaI agent system before Discord deployment.

## Prerequisites

### 1. MCP Servers

Start all 4 MCP servers in separate terminals:

```bash
# Terminal 1: Fundamentals Server
cd src/servers/fundamentals-server
uv run fastmcp run server.py
# Should start on http://localhost:8001

# Terminal 2: Market Data Server
cd src/servers/market-data-server
uv run fastmcp run server.py
# Should start on http://localhost:8002

# Terminal 3: Events/News Server
cd src/servers/events-news-server
uv run fastmcp run server.py
# Should start on http://localhost:8003

# Terminal 4: Options Server
cd src/servers/options-server
uv run fastmcp run server.py
# Should start on http://localhost:8004
```

**Alternative: Docker Compose** (if available)
```bash
cd src/servers
docker-compose up
```

### 2. Environment Variables

Create `.env` file or export variables:

```bash
# Required
export OPENAI_API_KEY=sk-proj-your-key-here

# MCP Server URLs (defaults shown, change if different)
export MCP_FUNDAMENTALS_URL=http://localhost:8001/mcp
export MCP_MARKET_DATA_URL=http://localhost:8002/mcp
export MCP_EVENTS_NEWS_URL=http://localhost:8003/mcp
export MCP_OPTIONS_URL=http://localhost:8004/mcp

# Optional: Model Configuration
export ORCHESTRATOR_MODEL=gpt-4o           # Default
export SPECIALIST_MODEL=gpt-4o-mini        # Default

# Optional: Guardrails (enabled by default)
export ENABLE_GUARDRAILS=true
```

You can copy from `.env.example`:
```bash
cd src/OBaI/clients/cli
cp .env.example .env
# Edit .env with your values
```

### 3. Install Dependencies

```bash
cd src/OBaI/agents
uv sync
```

## Testing Process

### Step 1: Test MCP Server Connectivity

Before running the chat client, verify all MCP servers are reachable:

```bash
# You can run from anywhere in the project
python src/OBaI/clients/cli/test_connection.py

# Or cd to the cli directory
cd src/OBaI/clients/cli
python test_connection.py
```

**Expected Output:**
```
============================================================
  MCP Server Connection Test
============================================================

Testing Fundamentals Server...
  URL: http://localhost:8001/mcp
  ✓ Connected
  ✓ 8 tools available
    Sample tools: get_company_profile, get_fundamentals, get_key_metrics

Testing Market Data Server...
  URL: http://localhost:8002/mcp
  ✓ Connected
  ✓ 9 tools available
    Sample tools: get_quote, get_latest_trade, get_candles

Testing Events/News Server...
  URL: http://localhost:8003/mcp
  ✓ Connected
  ✓ 3 tools available
    Sample tools: get_news_by_ticker, get_earnings_calendar, get_dividends_calendar

Testing Options Server...
  URL: http://localhost:8004/mcp
  ✓ Connected
  ✓ 3 tools available
    Sample tools: get_option_expiration_dates, get_options_by_strike, get_option_chain

============================================================
  Summary
============================================================

  ✓ PASS  Fundamentals Server
  ✓ PASS  Market Data Server
  ✓ PASS  Events/News Server
  ✓ PASS  Options Server

Total: 4/4 servers reachable

✓ All servers ready! You can now run chat.py
```

**If any server fails:**
1. Check that the server is actually running
2. Verify the URL in environment variables matches the server's port
3. Check for port conflicts (another process using the port)
4. Look at server logs for errors

### Step 2: Run Interactive Chat Client

```bash
# You can run from anywhere in the project
python src/OBaI/clients/cli/chat.py

# Or cd to the cli directory
cd src/OBaI/clients/cli
python chat.py
```

**Expected Output:**
```
============================================================
  OBaI - Financial Research Assistant
  (CLI Test Client)
============================================================

Type your question and press Enter.
Type 'quit' or 'exit' to end the session.
Type 'clear' to start a new conversation.

Central Hub Model: gpt-4o
Specialist Model: gpt-4o-mini

Initializing central hub and specialist agents...
✅ All agents ready!

✓ OpenAI Agent SDK available

You:
```

## Test Cases

### Test 1: Simple Quote Query (Market Data Agent)

**Query:** `What is AAPL trading at?`

**Expected Behavior:**
1. Central Hub analyzes query
2. Handoff to Market Data Agent
3. Market Data Agent calls `get_quote("AAPL")`
4. Response includes current price, change %, timestamp

**Expected Output Format:**
```
Processing...

🔍 Central Hub - working

OBaI: Apple (AAPL) is currently trading at $182.50, up 2.3% ($4.10) today.
      Last updated: 2026-01-13 15:45:00 EST
```

### Test 2: Fundamentals Query (Fundamentals Agent)

**Query:** `What is AAPL's P/E ratio?`

**Expected Behavior:**
1. Central Hub routes to Fundamentals Agent
2. Fundamentals Agent calls `get_financial_ratios("AAPL")`
3. Response includes P/E ratio and context

**Expected Output Format:**
```
OBaI: Apple's trailing P/E ratio is 28.5 (as of Q4 2025).
      This is [above/below] the sector average of 25.2.

      (Source: get_financial_ratios, 2026-01-13)
```

### Test 3: Multi-Agent Query

**Query:** `Analyze AAPL - show me price and financials`

**Expected Behavior:**
1. Central Hub identifies need for multiple specialists
2. Handoff to Market Data Agent for price
3. Handoff to Fundamentals Agent for financials
4. Central Hub synthesizes both responses

### Test 4: News/Events Query

**Query:** `Any recent news on TSLA?`

**Expected Behavior:**
1. Central Hub routes to Events/News Agent
2. Events Agent calls `get_news_by_ticker("TSLA", from_date=today)`
3. Response includes recent headlines with sources

### Test 5: Options Query

**Query:** `Show me AAPL call options expiring next month`

**Expected Behavior:**
1. Central Hub routes to Options Agent
2. Options Agent first calls `get_option_expiration_dates("AAPL")`
3. Then calls `get_options_by_strike()` for relevant expiration
4. Response includes strikes, premiums, Greeks

### Test 6: Input Guardrails (Valid Financial Query)

**Query:** `What are the top market movers today?`

**Expected Behavior:**
1. Guardrail validates as financial query
2. Passes through to Central Hub
3. Routes to Market Data Agent
4. Response shows top gainers/losers

### Test 7: Input Guardrails (Invalid Non-Financial Query)

**Query:** `What is the capital of France?`

**Expected Behavior:**
1. Guardrail detects non-financial query
2. Tripwire triggered
3. Friendly rejection message shown
4. No central hub call (saves API costs)

**Expected Output:**
```
🚫 I can only help with stock market research and financial analysis.

Your question doesn't seem to be about:
- Stock prices, quotes, or market data
- Company fundamentals or financial statements
- News, earnings, or corporate events
- Options chains or derivatives

Please ask a finance-related question!
```

### Test 8: Follow-Up Questions (Session Memory)

**First Query:** `What is NVDA trading at?`
**Second Query:** `Why did it move today?`

**Expected Behavior:**
1. First query: Market Data Agent provides quote
2. Session stores conversation context automatically
3. Second query: Central Hub understands "it" refers to NVDA from session history
4. Routes to Events/News Agent to check for catalysts
5. Response correlates news to price movement

**How to verify Session is working:**
- Agent should NOT ask "which stock?" on second query
- Response should reference NVDA without you re-stating the symbol
- Session automatically maintains context across turns

### Test 9: Error Handling (Invalid Symbol)

**Query:** `What is FAKESYMBOL trading at?`

**Expected Behavior:**
1. Market Data Agent attempts `get_quote("FAKESYMBOL")`
2. MCP server returns error (symbol not found)
3. Agent handles gracefully and informs user

**Expected Output:**
```
OBaI: I couldn't find any data for symbol "FAKESYMBOL".
      Please verify the ticker symbol is correct.
```

### Test 10: Session Clear Command

**Turn 1:** `What is AAPL trading at?`
**Turn 2:** `What's the P/E ratio?`
**Turn 3:** `clear` (command)
**Turn 4:** `What's the P/E ratio?`

**Expected Behavior:**
1. Turns 1-2: Agent knows context (AAPL)
2. Turn 3: Session cleared (new session created)
3. Turn 4: Agent asks "Which stock?" (no context after clear)

**How to verify:**
- Before clear: Follow-up works
- After clear: No memory of AAPL
- Proves Session is managing context correctly

### Test 11: Complex Multi-Turn Conversation

**Turn 1:** `What is AAPL trading at?`
**Turn 2:** `What's the P/E ratio?`
**Turn 3:** `Any news today?`
**Turn 4:** `Show me call options`

**Expected Behavior:**
All queries should understand "AAPL" context without re-stating the symbol.

## Verification Checklist

After testing, verify:

- [ ] All 4 MCP servers connected successfully
- [ ] Central Hub initialized without errors
- [ ] Session created successfully (see "✓ Session created" message)
- [ ] Market Data Agent can retrieve quotes
- [ ] Fundamentals Agent can retrieve financials
- [ ] Events/News Agent can retrieve news
- [ ] Options Agent can retrieve options data
- [ ] Guardrails reject non-financial queries
- [ ] Guardrails allow financial queries
- [ ] **Session Memory**: Follow-up questions work (automatic context)
- [ ] **Session Clear**: `clear` command resets context
- [ ] Error messages are user-friendly
- [ ] All responses include data sources and timestamps
- [ ] No crashes or unhandled exceptions
- [ ] Agent handoffs work correctly
- [ ] Response times are reasonable (<10s for simple queries)

## Common Issues

### Issue: "Failed to initialize central hub"

**Cause:** Missing OPENAI_API_KEY or invalid key

**Fix:**
```bash
export OPENAI_API_KEY=sk-proj-your-actual-key
```

### Issue: "Connection refused" errors

**Cause:** MCP server not running or wrong port

**Fix:**
1. Check server is running: `lsof -i :8001` (repeat for 8002-8004)
2. Start missing servers
3. Verify URLs in environment variables match server ports

### Issue: "OpenAI Agent SDK not available"

**Cause:** Missing OpenAI SDK installation or old version

**Fix:**
```bash
cd src/OBaI/agents
uv add "openai>=1.0.0"
uv sync
```

### Issue: Guardrails not working

**Cause:** Guardrails disabled or guardrail agent not initialized

**Fix:**
```bash
export ENABLE_GUARDRAILS=true
```

Then restart chat.py.

### Issue: Agent responses are slow

**Possible Causes:**
- Using expensive models (gpt-4o for specialists)
- MCP server network latency
- Large tool responses (e.g., full option chains)

**Optimizations:**
```bash
# Use cheaper models for specialists
export SPECIALIST_MODEL=gpt-4o-mini

# Keep central hub as gpt-4o (needs reasoning)
export ORCHESTRATOR_MODEL=gpt-4o
```

### Issue: Type errors or import errors

**Cause:** Missing `agents/__init__.py` or wrong directory structure

**Fix:**
```bash
# Verify agents package exists
ls src/OBaI/agents/__init__.py

# Should show the file - if not, agents isn't properly set up as a package
```

The scripts auto-detect the OBaI root directory, so you can run from anywhere.

## Performance Metrics

Track these during testing:

**Response Time Targets:**
- Simple quote: < 3 seconds
- Fundamentals query: < 5 seconds
- Multi-agent query: < 10 seconds
- Complex analysis: < 20 seconds

**Cost per Query (Estimated):**
- With guardrails (non-financial rejected): ~$0.00015 (guardrail only)
- With guardrails (financial query): ~$0.00515 (guardrail + central hub + specialist)
- Without guardrails: ~$0.005 (central hub + specialist)

**Tool Call Success Rate:**
- Target: > 95% successful tool calls
- Track failures and investigate MCP server issues

## Next Steps

Once CLI testing is complete and all test cases pass:

1. ✅ Mark "Test all 5 agents with CLI client" as complete
2. ✅ Mark "Verify agent handoffs work correctly" as complete
3. ⏳ Add discord.py and boto3 dependencies
4. ⏳ Implement Discord bot client
5. ⏳ Implement /research command handler
6. ⏳ Create services/ directory (session_manager, analytics, symbol_service)

## Logging Output for Debugging

If you encounter issues, check logs:

```bash
# Set log level to DEBUG for detailed output
export LOG_LEVEL=DEBUG

# Run chat client - will show detailed agent reasoning
python chat.py
```

This will show:
- Agent SDK tool calls and responses
- MCP client HTTP requests/responses
- Guardrail validation details
- Agent handoff decisions

## Reporting Issues

If you find bugs or unexpected behavior:

1. Note the exact query that caused the issue
2. Copy the full error message or unexpected output
3. Check MCP server logs for errors
4. Include environment details (models used, guardrails enabled, etc.)
5. Try to reproduce with a simpler query

---

**Status:** CLI client ready for testing
**Last Updated:** 2026-01-13
