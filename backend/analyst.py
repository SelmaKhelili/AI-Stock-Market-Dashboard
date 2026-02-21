"""
analyst.py — AI stock analyst using Groq LLaMA with function calling

Function calling = the LLM decides which tools to call to answer a question.
Instead of us hardcoding "fetch price then fetch news then answer",
the model figures out what it needs and calls the right functions.

Tools available to the LLM:
- get_stock_price(ticker) → current price + change
- get_stock_news(ticker) → latest headlines
- get_price_history(ticker, range) → historical prices
- get_company_info(ticker) → company description
"""
import os
import json
from groq import Groq
from typing import Generator
import asyncio

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# ── Tool definitions — what the LLM can call ──────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_stock_price",
            "description": "Get the current price, change, and key stats for a stock ticker. Use this when asked about current price, performance, or how a stock is doing today.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol e.g. AAPL, NVDA, TSLA"}
                },
                "required": ["ticker"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_news",
            "description": "Get recent news headlines and summaries for a stock. Use this when asked about news, recent events, why a stock moved, or market sentiment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol"},
                    "limit": {"type": "integer", "description": "Number of articles to fetch (default 5)", "default": 5}
                },
                "required": ["ticker"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_company_info",
            "description": "Get company description, sector, market cap, and employee count. Use when asked about what a company does or its background.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol"}
                },
                "required": ["ticker"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_history",
            "description": "Get historical price data for trend analysis. Use when asked about performance over time, trends, or technical analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol"},
                    "range": {
                        "type": "string",
                        "enum": ["1D", "1W", "1M", "1Y"],
                        "description": "Time range for history"
                    }
                },
                "required": ["ticker"]
            }
        }
    }
]

SYSTEM_PROMPT = """You are a sharp, knowledgeable stock market analyst. You have access to real-time market data tools.

Guidelines:
- Always fetch relevant data before answering — don't rely on training knowledge for prices or news
- Be direct and specific — give numbers, percentages, concrete observations
- Note that prices are 15-minute delayed (free API tier)
- Flag when you're giving opinion vs fact
- Keep responses concise but insightful — no fluff
- If asked about multiple stocks, fetch data for each
- Format numbers cleanly: $182.45, +2.3%, $2.8T market cap
- Never give financial advice — state observations and let the user decide"""


async def execute_tool(tool_name: str, args: dict) -> str:
    """Execute the tool the LLM requested and return result as string."""
    from market import get_snapshot, get_news, get_ticker_details, get_price_history

    try:
        if tool_name == "get_stock_price":
            data = await get_snapshot(args["ticker"])
            if not data:
                return f"No data found for {args['ticker']}"
            return json.dumps(data)

        elif tool_name == "get_stock_news":
            articles = await get_news(args["ticker"], args.get("limit", 5))
            if not articles:
                return f"No recent news found for {args['ticker']}"
            return json.dumps(articles)

        elif tool_name == "get_company_info":
            data = await get_ticker_details(args["ticker"])
            if not data:
                return f"No company info found for {args['ticker']}"
            return json.dumps(data)

        elif tool_name == "get_price_history":
            bars = await get_price_history(args["ticker"], args.get("range", "1M"))
            if not bars:
                return f"No price history found for {args['ticker']}"
            # Summarize for LLM — don't send 365 bars of raw data
            if len(bars) > 10:
                first = bars[0]
                last = bars[-1]
                high = max(b["high"] for b in bars)
                low = min(b["low"] for b in bars)
                change = ((last["close"] - first["open"]) / first["open"] * 100) if first["open"] else 0
                return json.dumps({
                    "range": args.get("range", "1M"),
                    "start_price": first["open"],
                    "end_price": last["close"],
                    "high": high,
                    "low": low,
                    "change_pct": round(change, 2),
                    "bars": len(bars)
                })
            return json.dumps(bars)

    except Exception as e:
        return f"Tool error: {str(e)}"


async def stream_analyst_response(query: str, context_ticker: str | None, history: list[dict]) -> Generator:
    """
    Stream AI analyst response using function calling.
    
    The flow:
    1. Send query + tools to LLM
    2. LLM decides which tools to call
    3. We execute the tools and return results
    4. LLM generates final answer with the data
    5. Stream tokens to client
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Add context about currently viewed stock
    if context_ticker:
        messages.append({
            "role": "system",
            "content": f"The user is currently viewing {context_ticker} on the dashboard."
        })

    # Add chat history (last 6 messages)
    messages.extend(history[-6:])
    messages.append({"role": "user", "content": query})

    # ── Round 1: Let LLM decide which tools to call ───────────
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        max_tokens=1024,
        temperature=0.3,
    )

    assistant_msg = response.choices[0].message

    # ── Round 2: Execute all requested tool calls ─────────────
    if assistant_msg.tool_calls:
        messages.append({
            "role": "assistant",
            "content": assistant_msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                }
                for tc in assistant_msg.tool_calls
            ]
        })

        # Execute all tool calls
        for tool_call in assistant_msg.tool_calls:
            args = json.loads(tool_call.function.arguments)
            result = await execute_tool(tool_call.function.name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    # ── Round 3: Stream final answer with fetched data ────────
    stream = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        stream=True,
        max_tokens=800,
        temperature=0.3,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
