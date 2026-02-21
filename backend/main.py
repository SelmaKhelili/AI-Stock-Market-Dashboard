"""
main.py — FastAPI backend for StockAI
Endpoints:
  GET  /stock/{ticker}          — full stock data (price + details + news)
  GET  /stock/{ticker}/history  — price history for chart
  GET  /search                  — search tickers
  POST /chat                    — AI analyst with function calling (streaming)
  GET  /                        — serve frontend
"""
import os
import json
import pathlib
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from market import get_snapshot, get_ticker_details, get_news, get_price_history, search_tickers
from analyst import stream_analyst_response

BASE_DIR = pathlib.Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

@app.get("/")
async def root():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


# ── Stock data ────────────────────────────────────────────────
@app.get("/stock/{ticker}")
async def get_stock(ticker: str):
    ticker = ticker.upper()
    try:
        snapshot, details, news = await __import__('asyncio').gather(
            get_snapshot(ticker),
            get_ticker_details(ticker),
            get_news(ticker, limit=6),
        )
        return {"ticker": ticker, "snapshot": snapshot, "details": details, "news": news}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stock/{ticker}/history")
async def get_history(ticker: str, range: str = "1M"):
    ticker = ticker.upper()
    if range not in ["1D", "1W", "1M", "1Y"]:
        raise HTTPException(status_code=400, detail="Invalid range")
    try:
        bars = await get_price_history(ticker, range)
        return {"ticker": ticker, "range": range, "bars": bars}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Search ────────────────────────────────────────────────────
@app.get("/search")
async def search(q: str):
    if not q.strip():
        return {"results": []}
    results = await search_tickers(q)
    return {"results": results}


# ── AI Analyst chat ───────────────────────────────────────────
class ChatRequest(BaseModel):
    query: str
    context_ticker: str | None = None
    history: list[dict] = []

@app.post("/chat")
async def chat(req: ChatRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Empty query")

    async def generate():
        import asyncio
        import queue
        import threading

        token_queue = queue.Queue()

        def run_analyst():
            async def _run():
                try:
                    async for token in stream_analyst_response(
                        req.query, req.context_ticker, req.history
                    ):
                        token_queue.put(token)
                except Exception as e:
                    token_queue.put(("ERROR", str(e)))
                finally:
                    token_queue.put(None)
            asyncio.run(_run())

        threading.Thread(target=run_analyst, daemon=True).start()

        while True:
            token = await asyncio.to_thread(token_queue.get)
            if token is None:
                break
            if isinstance(token, tuple) and token[0] == "ERROR":
                yield f"data: {json.dumps({'type': 'error', 'message': token[1]})}\n\n"
                break
            yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
