import requests
from bs4 import BeautifulSoup
from urllib.robotparser import RobotFileParser
import time
import os
from urllib.parse import urlparse, urljoin
import logging
from ddgs import DDGS
from tavily import TavilyClient

logging.basicConfig(level=logging.INFO)

USER_AGENT = os.getenv("USER_AGENT", "MyBot/1.0")
TARGET_SITES = [s.strip() for s in os.getenv("TARGET_SITES", "").split(",") if s.strip()]
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# Initialize Tavily client if key exists
tavily_client = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None

# ==================== ROBOTS.TXT CHECK ====================
def is_allowed(url: str) -> bool:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    rp = RobotFileParser()
    rp.set_url(urljoin(base, "/robots.txt"))
    try:
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception as e:
        logging.warning(f"Could not read robots.txt for {base}: {e}")
        return True

# ==================== TAVILY SEARCH (Primary) ====================
def search_web_tavily(query: str, max_results: int = 30) -> list[dict]:
    """Use Tavily to search and get structured results (title, description, URL)."""
    if not tavily_client:
        return []

    try:
        print(f"DEBUG: Searching Tavily for '{query}'")
        response = tavily_client.search(
            query=query,
            max_results=max_results,
            include_raw_content=False,   # We only need title+description+URL
            include_domains=TARGET_SITES if TARGET_SITES else None
        )
        
        results = response.get("results", [])
        print(f"DEBUG: Tavily returned {len(results)} results")
        
        # Convert to your product format
        products = []
        for r in results:
            products.append({
                "title": r.get("title", ""),
                "description": r.get("content", ""),
                "url": r.get("url", ""),
                "price": None,  # Tavily doesn't extract price, but we can try later
                "availability": True,
                "source": "tavily"
            })
        return products
    except Exception as e:
        logging.error(f"Tavily search failed: {e}")
        return []

# ==================== DUCKDUCKGO SEARCH (Fallback) ====================
def search_web_duckduckgo(query: str, max_results: int = 30) -> list[str]:
    """Use DuckDuckGo to get raw URLs (fallback)."""
    urls = []
    try:
        with DDGS() as ddgs:
            for result in ddgs.text(query, max_results=max_results):
                link = result.get("href")
                if link:
                    if TARGET_SITES:
                        if any(site in link for site in TARGET_SITES):
                            urls.append(link)
                    else:
                        urls.append(link)
                    if len(urls) >= max_results:
                        break
        print(f"DEBUG: DuckDuckGo returned {len(urls)} raw URLs")
    except Exception as e:
        logging.error(f"DuckDuckGo search failed: {e}")
    return urls

# ==================== SCRAPE A SINGLE PAGE (for DDG results) ====================
def scrape_product_page(url: str) -> dict | None:
    """Auto-detect title, description, price from any page."""
    if not is_allowed(url):
        logging.info(f"Skipping {url} due to robots.txt")
        return None

    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        logging.warning(f"Failed to fetch {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    
    # Auto-detect Title
    title = None
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    else:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
        elif soup.title:
            title = soup.title.string.strip()

    # Auto-detect Description
    description = ""
    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        description = og_desc["content"].strip()
    else:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            description = meta_desc["content"].strip()

    # Auto-detect Price
    price = None
    og_price = soup.find("meta", attrs={"property": "og:price:amount"})
    if og_price and og_price.get("content"):
        try:
            price = float(og_price["content"])
        except:
            pass
    
    if price is None:
        price_meta = soup.find("meta", attrs={"itemprop": "price"})
        if price_meta and price_meta.get("content"):
            try:
                price = float(price_meta["content"])
            except:
                pass
    
    if price is None:
        import re
        price_elem = soup.find(class_=re.compile(r"price|Price|amount|Amount", re.I))
        if price_elem:
            price_text = price_elem.get_text(strip=True)
            match = re.search(r'[\d,.]+', price_text)
            if match:
                try:
                    price = float(match.group().replace(',', ''))
                except:
                    pass

    if not title and not description:
        return None

    return {
        "title": title,
        "description": description or title,
        "price": price,
        "url": url,
        "availability": True,
        "source": "ddg"
    }

# ==================== MAIN FETCH FUNCTION (Hybrid) ====================
def fetch_products(query: str, max_candidates: int = 30, progress_callback=None) -> list[dict]:
    """Try Tavily first, then fallback to DuckDuckGo scraping."""
    products = []

    # --- Try Tavily (Primary) ---
    if tavily_client:
        if progress_callback:
            progress_callback(40, "Searching Tavily...")
        tavily_results = search_web_tavily(query, max_results=max_candidates)
        if tavily_results:
            products = [p for p in tavily_results if p.get("title")]
            if products:
                print(f"✅ Tavily returned {len(products)} products (skipping scraping!)")
                if progress_callback:
                    progress_callback(80, f"Tavily found {len(products)} products")
                return products[:max_candidates]

    # --- Fallback: DuckDuckGo + Scraping ---
    if progress_callback:
        progress_callback(45, "Falling back to DuckDuckGo + scraping...")
    print("🔄 Tavily returned 0 results or not configured – falling back to DuckDuckGo + scraping.")
    urls = search_web_duckduckgo(query, max_results=max_candidates)
    scraped = []
    total = len(urls)
    for idx, url in enumerate(urls):
        if progress_callback:
            progress_callback(45 + int(40 * (idx / total)), f"Scraping {idx+1}/{total}...")
        prod = scrape_product_page(url)
        if prod and prod.get("availability", False):
            scraped.append(prod)
        time.sleep(1)
        if len(scraped) >= max_candidates:
            break
    
    print(f"🔍 DuckDuckGo fallback scraped {len(scraped)} products")
    if progress_callback:
        progress_callback(100, "Done.")
    return scraped