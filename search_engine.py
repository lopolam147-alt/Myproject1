import requests
from bs4 import BeautifulSoup
from urllib.robotparser import RobotFileParser
import time
import os
from urllib.parse import urlparse, urljoin
import logging
from ddgs import DDGS
from tavily import TavilyClient
import concurrent.futures

logging.basicConfig(level=logging.INFO)

USER_AGENT = os.getenv("USER_AGENT", "MyBot/1.0")
TARGET_SITES = [s.strip() for s in os.getenv("TARGET_SITES", "").split(",") if s.strip()]
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
FIRECRAWL_API_URL = "https://api.firecrawl.dev/v1/scrape"


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
            include_raw_content=True,   # We only need title+description+URL
            include_domains=TARGET_SITES if TARGET_SITES else None
        )

        

        results = response.get("results", [])
        print(f"DEBUG: Tavily returned {len(results)} results")
        
        # Convert to your product format
        products = []
        for r in results:
            if 'github.com' in r.get('url', ''):
                continue
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
    
def fetch_with_firecrawl(url: str, timeout: int = 20) -> str | None:
    if not FIRECRAWL_API_KEY:
        return None
    payload = {
        "url": url,
        "render": True,
        "headers": {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        }
    }
    headers = {"Authorization": f"Bearer {FIRECRAWL_API_KEY}", "Content-Type": "application/json"}
    try:
        resp = requests.post(FIRECRAWL_API_URL, json=payload, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("data", {}).get("markdown") or data.get("data", {}).get("content")
    except Exception:
        pass
    return None
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
# ==================== 检测页面是否包含不可用关键词 ====================

def check_unavailable(url: str) -> bool:
    unavailable_keywords = [
        "cannot be shipped", "out of stock"
        "sold out", "currently unavailable"
    ]
    print(f"🔍 开始检测: {url[:60]}...")  # 显示正在检测的 URL

    if FIRECRAWL_API_KEY:
        try:
            markdown = fetch_with_firecrawl(url, timeout=6)
            if markdown:
                content = markdown.lower()
                for kw in unavailable_keywords:
                    if kw in content:
                        print(f"   ✅ Firecrawl 找到关键词: {kw}")
                        return True
                print(f"   ❌ Firecrawl 未找到关键词")
                return False
        except Exception as e:
            print(f"   ⚠️ Firecrawl 异常: {e}")

    # fallback 到 requests
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code != 200:
            print(f"   ❌ requests 状态码 {resp.status_code}")
            return False
        content = resp.text.lower()
        for kw in unavailable_keywords:
            if kw in content:
                print(f"   ✅ requests 找到关键词: {kw}")
                return True
        print(f"   ❌ requests 未找到关键词")
        return False
    except Exception as e:
        print(f"   ⚠️ requests 异常: {e}")
        return False
    
# ==================== MAIN FETCH FUNCTION (Hybrid) ====================
def fetch_products(query: str, max_candidates: int = 30, progress_callback=None, keywords: list = None) -> list[dict]:
    """Try Tavily first (multiple variants), then fallback to DuckDuckGo scraping."""

    # --- 1. 生成查询变体（最多3个） ---
    queries = [query]
    if keywords:
        for kw in keywords[:2]:  # 取前2个关键词
            if kw not in query:  # 避免重复
                queries.append(f"{query} {kw}")

    unique_queries = list(dict.fromkeys(queries))[:3]
    print(f"🔍 将执行 {len(unique_queries)} 次搜索: {unique_queries}")

    # --- 2. 执行多次搜索 ---
    all_products = []
    for idx, q in enumerate(unique_queries):
        if progress_callback:
            progress_callback(40 + int(20 * idx / len(unique_queries)), f"Searching Tavily with '{q[:30]}...'")
        results = search_web_tavily(q, max_results=30)
        if results:
            all_products.extend(results)
            print(f"✅ 搜索 '{q}' 获得 {len(results)} 个结果")
        # 礼貌延迟（第1次不延迟，后续延迟1秒递增）
        if idx < len(unique_queries) - 1:
            time.sleep(1 + idx)  # 第1次后等1秒，第2次后等2秒

    # --- 3. 去重（按URL） ---
    seen_urls = set()
    unique_products = []
    for p in all_products:
        url = p.get("url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_products.append(p)

    print(f"✅ 合并去重后共有 {len(unique_products)} 个唯一商品")

    # --- 4. 并发检测不可用（去重后） ---
        # --- 4. 并发检测不可用（去重后） ---
    CHECK_LIMIT = len(unique_products)
    if unique_products:
        import concurrent.futures
        print(f"🔍 开始并发检测 {min(CHECK_LIMIT, len(unique_products))} 个商品...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            # 提交所有检测任务
            future_to_product = {
                executor.submit(check_unavailable, p['url']): p 
                for p in unique_products[:CHECK_LIMIT] 
                if p.get('url')
            }
            print(f"   已提交 {len(future_to_product)} 个检测任务")

            for future in concurrent.futures.as_completed(future_to_product):
                p = future_to_product[future]
                try:
                    is_unavailable = future.result()
                    p['unavailable'] = is_unavailable
                    if is_unavailable:
                        print(f"   ⛔ 检测到不可用: {p.get('title', '')[:30]}...")
                except Exception as e:
                    print(f"   ⚠️ 检测任务异常: {e}")
                    p['unavailable'] = False

        # --- 5. 返回结果 ---
        if unique_products:
            if progress_callback:
                progress_callback(80, f"Tavily found {len(unique_products)} products")
            return unique_products[:max_candidates]
    
    # --- 5. Fallback: DuckDuckGo + Scraping ---
    if progress_callback:
        progress_callback(45, "Falling back to DuckDuckGo + scraping...")
    print("🔄 Tavily returned 0 results or not configured - falling back to DuckDuckGo + scraping.")
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