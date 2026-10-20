import logging
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple
import httpx
from bs4 import BeautifulSoup
from google.genai import types

from .config import ALPHA_VANTAGE_API_KEY

logger = logging.getLogger("jarvis.tools")

SEARCH_WEB_DECLARATION = types.FunctionDeclaration(
    name="search_web",
    description="Searches the live web for current events, real-time facts, news, and up-to-date information across the internet.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The search query keywords to search on the web."
            )
        },
        required=["query"],
    )
)

SCRAPE_WEBPAGE_DECLARATION = types.FunctionDeclaration(
    name="scrape_webpage",
    description="Scrapes and extracts the readable text content of a specified web page URL for research and answering questions.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "url": types.Schema(
                type=types.Type.STRING,
                description="The full HTTP/HTTPS URL of the webpage to scrape."
            )
        },
        required=["url"],
    )
)

GET_STOCK_QUOTE_DECLARATION = types.FunctionDeclaration(
    name="get_stock_quote",
    description=(
        "Retrieves real-time stock market quotes, prices, daily change percentage, day high/low, and trading volume "
        "using Alpha Vantage. Supports stock ticker symbols (e.g. 'AAPL', 'TSLA', 'NVDA', 'MSFT', 'RELIANCE.BSE') "
        "or company names (e.g. 'Apple', 'Tesla', 'Nvidia', 'Tata Motors')."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "symbol": types.Schema(
                type=types.Type.STRING,
                description="The stock ticker symbol or company name (e.g. 'AAPL', 'NVDA', 'Tesla', 'Microsoft')."
            )
        },
        required=["symbol"],
    )
)

GET_CURRENCY_EXCHANGE_RATE_DECLARATION = types.FunctionDeclaration(
    name="get_currency_exchange_rate",
    description=(
        "Retrieves real-time foreign exchange (Forex) and cryptocurrency exchange rates "
        "using Alpha Vantage (e.g. USD to INR, EUR to USD, BTC to USD, ETH to INR)."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "from_currency": types.Schema(
                type=types.Type.STRING,
                description="The source currency or cryptocurrency symbol (e.g. 'USD', 'EUR', 'GBP', 'BTC', 'ETH')."
            ),
            "to_currency": types.Schema(
                type=types.Type.STRING,
                description="The target currency symbol (e.g. 'INR', 'USD', 'EUR', 'JPY')."
            ),
        },
        required=["from_currency", "to_currency"],
    )
)

# Common company name to ticker mapping for instant resolution
COMMON_COMPANY_TICKERS = {
    "apple": "AAPL",
    "tesla": "TSLA",
    "microsoft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "nvidia": "NVDA",
    "meta": "META",
    "facebook": "META",
    "netflix": "NFLX",
    "reliance": "RELIANCE.BSE",
    "tcs": "TCS.BSE",
    "infosys": "INFY",
    "tata motors": "TATAMOTORS.BSE",
    "tata steel": "TATASTEEL.BSE",
    "hdfc": "HDFCBANK.BSE",
    "icici": "ICICIBANK.BSE",
    "ibm": "IBM",
    "amd": "AMD",
    "intel": "INTC",
    "bitcoin": "BTC",
    "ethereum": "ETH",
}

# In-memory caches to protect Alpha Vantage rate limits (5-minute TTL)
_stock_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_forex_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
CACHE_TTL = 300.0


def resolve_symbol(query: str) -> str:
    """Resolve company name or user query into an Alpha Vantage ticker symbol."""
    cleaned = query.strip().lower()
    if cleaned in COMMON_COMPANY_TICKERS:
        return COMMON_COMPANY_TICKERS[cleaned]

    # If already a valid uppercase ticker e.g. "AAPL", "NVDA", "RELIANCE.BSE"
    if query.strip().isupper() and len(query.strip()) <= 12 and " " not in query:
        return query.strip()

    # Try Alpha Vantage symbol search
    if ALPHA_VANTAGE_API_KEY:
        try:
            url = f"https://www.alphavantage.co/query?function=SYMBOL_SEARCH&keywords={urllib.parse.quote(query)}&apikey={ALPHA_VANTAGE_API_KEY}"
            with httpx.Client(timeout=5.0) as client:
                data = client.get(url).json()
            matches = data.get("bestMatches", [])
            if matches:
                top_sym = matches[0].get("1. symbol")
                if top_sym:
                    return top_sym
        except Exception as e:
            logger.debug(f"Symbol search failed: {e}")

    return query.strip().upper()


def execute_get_stock_quote(symbol: str) -> Dict[str, Any]:
    """Fetch real-time stock quote via Alpha Vantage with caching and smart fallback."""
    resolved_sym = resolve_symbol(symbol)
    now = time.time()

    # 1. Check in-memory cache
    if resolved_sym in _stock_cache:
        cached_time, cached_data = _stock_cache[resolved_sym]
        if now - cached_time < CACHE_TTL:
            logger.debug(f"Returning cached stock quote for {resolved_sym}")
            return cached_data

    # 2. Query Alpha Vantage API
    if ALPHA_VANTAGE_API_KEY:
        try:
            url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={urllib.parse.quote(resolved_sym)}&apikey={ALPHA_VANTAGE_API_KEY}"
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(url).json()

            quote = resp.get("Global Quote", {})
            if quote and "05. price" in quote:
                price = float(quote.get("05. price", 0.0))
                change = float(quote.get("09. change", 0.0))
                change_pct = quote.get("10. change percent", "0%")
                high = float(quote.get("03. high", 0.0))
                low = float(quote.get("04. low", 0.0))
                volume = quote.get("06. volume", "0")
                day = quote.get("07. latest trading day", "")

                currency_sym = "₹" if (".BSE" in resolved_sym or ".NSE" in resolved_sym) else "$"
                result = {
                    "status": "success",
                    "source": "Alpha Vantage",
                    "symbol": resolved_sym,
                    "price": f"{currency_sym}{price:,.2f}",
                    "change": f"{change:+,.2f}",
                    "change_percent": change_pct,
                    "high": f"{currency_sym}{high:,.2f}",
                    "low": f"{currency_sym}{low:,.2f}",
                    "volume": volume,
                    "trading_day": day,
                    "summary": (
                        f"{resolved_sym} is currently trading at {currency_sym}{price:,.2f} "
                        f"({change:+,.2f}, {change_pct}) with a day high of {currency_sym}{high:,.2f} "
                        f"and low of {currency_sym}{low:,.2f}."
                    )
                }
                _stock_cache[resolved_sym] = (now, result)
                return result

            if "Information" in resp:
                logger.info(f"Alpha Vantage rate limit reached: {resp['Information']}. Falling back to live web search.")
        except Exception as e:
            logger.warning(f"Alpha Vantage request failed for {resolved_sym}: {e}")

    # 3. Fallback: Live Web Search Grounding
    search_res = execute_search_web(f"{symbol} stock price today quote")
    return {
        "status": "success",
        "source": "Live Market Search Fallback",
        "symbol": resolved_sym,
        "market_info": search_res.get("results", []),
        "summary": f"Retrieved live market data for {symbol}."
    }


def execute_get_currency_rate(from_curr: str, to_curr: str) -> Dict[str, Any]:
    """Fetch real-time Forex or Cryptocurrency exchange rate via Alpha Vantage."""
    from_curr = from_curr.strip().upper()
    to_curr = to_curr.strip().upper()
    cache_key = f"{from_curr}_{to_curr}"
    now = time.time()

    # 1. Check in-memory cache
    if cache_key in _forex_cache:
        cached_time, cached_data = _forex_cache[cache_key]
        if now - cached_time < CACHE_TTL:
            logger.debug(f"Returning cached forex rate for {cache_key}")
            return cached_data

    # 2. Query Alpha Vantage API
    if ALPHA_VANTAGE_API_KEY:
        try:
            url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency={from_curr}&to_currency={to_curr}&apikey={ALPHA_VANTAGE_API_KEY}"
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(url).json()

            rate_data = resp.get("Realtime Currency Exchange Rate", {})
            if rate_data and "5. Exchange Rate" in rate_data:
                rate = float(rate_data.get("5. Exchange Rate", 0.0))
                from_name = rate_data.get("2. From_Currency Name", from_curr)
                to_name = rate_data.get("4. To_Currency Name", to_curr)
                refresh_time = rate_data.get("6. Last Refreshed", "")

                result = {
                    "status": "success",
                    "source": "Alpha Vantage",
                    "from_currency": from_curr,
                    "to_currency": to_curr,
                    "exchange_rate": rate,
                    "last_refreshed": refresh_time,
                    "summary": f"1 {from_name} ({from_curr}) is currently equal to {rate:,.4f} {to_name} ({to_curr})."
                }
                _forex_cache[cache_key] = (now, result)
                return result

            if "Information" in resp:
                logger.info(f"Alpha Vantage rate limit reached for {cache_key}. Falling back to web search.")
        except Exception as e:
            logger.warning(f"Alpha Vantage currency lookup failed for {cache_key}: {e}")

    # 3. Fallback: Live Web Search Grounding
    search_res = execute_search_web(f"{from_curr} to {to_curr} exchange rate today")
    return {
        "status": "success",
        "source": "Live FX Search Fallback",
        "from_currency": from_curr,
        "to_currency": to_curr,
        "market_info": search_res.get("results", []),
        "summary": f"Retrieved live conversion rate between {from_curr} and {to_curr}."
    }


def execute_search_web(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    Search the live web using DuckDuckGo without requiring third-party API keys or billing.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    url = "https://html.duckduckgo.com/html/"
    try:
        with httpx.Client(headers=headers, timeout=8.0, follow_redirects=True) as client:
            resp = client.post(url, data={"q": query})
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for r in soup.select(".result__body"):
            title_elem = r.select_one(".result__title a")
            snippet_elem = r.select_one(".result__snippet")
            if title_elem and snippet_elem:
                title = title_elem.get_text(strip=True)
                link = title_elem.get("href", "")
                if "uddg=" in link:
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
                    if "uddg" in parsed:
                        link = parsed["uddg"][0]
                snippet = snippet_elem.get_text(strip=True)
                results.append({"title": title, "url": link, "snippet": snippet})
                if len(results) >= max_results:
                    break

        if not results:
            return {"status": "empty", "query": query, "message": "No search results found."}

        return {"status": "success", "query": query, "results": results}
    except Exception as exc:
        logger.warning(f"Web search failed for '{query}': {exc}")
        return {"status": "error", "query": query, "error": str(exc)}


def execute_scrape_webpage(url: str, max_chars: int = 3500) -> Dict[str, Any]:
    """
    Fetch and extract clean readable text from a target web URL.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove irrelevant and clutter elements
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "form"]):
            tag.decompose()

        # Extract title and body content
        title = soup.title.string.strip() if (soup.title and soup.title.string) else ""
        text = " ".join(soup.stripped_strings)

        if len(text) > max_chars:
            text = text[:max_chars] + "... [truncated]"

        return {
            "status": "success",
            "url": url,
            "title": title,
            "content": text
        }
    except Exception as exc:
        logger.warning(f"Failed to scrape webpage {url}: {exc}")
        return {
            "status": "error",
            "url": url,
            "error": str(exc)
        }


def handle_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Route function calls executed by Gemini Live."""
    if name == "search_web":
        query = args.get("query", "")
        return execute_search_web(query)
    elif name == "scrape_webpage":
        url = args.get("url", "")
        return execute_scrape_webpage(url)
    elif name == "get_stock_quote":
        symbol = args.get("symbol", "")
        return execute_get_stock_quote(symbol)
    elif name == "get_currency_exchange_rate":
        from_curr = args.get("from_currency", "")
        to_curr = args.get("to_currency", "")
        return execute_get_currency_rate(from_curr, to_curr)
    return {"status": "error", "message": f"Unknown tool: {name}"}
