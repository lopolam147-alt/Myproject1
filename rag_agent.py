"""
RAG Agent for Electronic Device Recommender
This module implements the core retrieval-augmented generation pipeline.
"""

from utils import build_query_from_input
from database import get_cached_results, cache_results, delete_old_entries
from search_engine import fetch_products
from embedder import get_embedding, cosine_similarity
import os
from datetime import datetime
import logging
import requests
import re

logging.basicConfig(level=logging.INFO)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", 90))
TOP_K = int(os.getenv("TOP_K", 10))

class RAGAgent:
    def __init__(self):
        self.embedding_model = get_embedding  # alias

    def process_request(self, user_input: dict, progress_callback=None):
        try:
            if progress_callback:
                progress_callback(0, "Cleaning input and extracting keywords...")
            query_text = build_query_from_input(user_input)
            if not query_text:
                return {"recommendations": [], "message": "No valid keywords extracted."}

            if progress_callback:
                progress_callback(20, "Checking cache...")

            refresh = user_input.get("refresh", False)
            if not refresh:
                cached = get_cached_results(query_text)
                if cached is not None:
                    if progress_callback:
                        progress_callback(100, "Returning cached results.")
                    return {
                        "recommendations": cached,
                        "source": "cache",
                        "user_input": {
                            "device_type": user_input.get("device_type"),
                            "brands": user_input.get("brands"),
                            "color": user_input.get("color"),
                            "version": user_input.get("version"),
                            "others": user_input.get("others")
                        }
                    }
            
            if progress_callback:
                progress_callback(40, f"Searching web for '{query_text}'...")

            keywords = []
            for field in ["brands", "version", "color"]:
                val = user_input.get(field)
                if val:
                    keywords.extend(val.split())
            device_type = user_input.get("device_type")
            if device_type:
                keywords.append(device_type)
            keywords = [k for k in keywords if len(k) > 1]
            print(f"🔍 Extracted keywords: {keywords}")

            candidates = fetch_products(
                query_text,
                max_candidates=MAX_CANDIDATES,
                progress_callback=progress_callback,
                keywords=keywords
            )
            if not candidates:
                if progress_callback:
                    progress_callback(100, "No products found.")
                return {"recommendations": [], "message": "No products found."}

            if progress_callback:
                progress_callback(60, f"Found {len(candidates)} candidates. Computing embeddings...")

            query_emb = get_embedding(query_text)
            for prod in candidates:
                text_for_embed = prod.get("title", "") + " " + prod.get("description", "")
                prod_emb = get_embedding(text_for_embed)
                prod["similarity"] = cosine_similarity(query_emb, prod_emb)

            # ---------- Shopping intent detection ----------
            user_text = " ".join([
                user_input.get("device_type", ""),
                user_input.get("brands", ""),
                user_input.get("version", ""),
                user_input.get("color", ""),
                user_input.get("others", "")
            ]).lower()

            shopping_signals = ["$", "hk$", "us$", "buy", "price", "shop", "deal", "discount", "purchase", "cart", "checkout"]
            has_shopping_intent = any(signal in user_text for signal in shopping_signals)
            print(f"🛒 Shopping intent detected: {has_shopping_intent} (User input: {user_text[:50]}...)")

            print(f"🛒 Check price presence for {len(candidates)} candidates")
            for c in candidates:
                price = c.get("price")

            # ---------- Filter out obsolete products (year < current_year - 1) ----------
            current_year = datetime.now().year
            filtered_candidates = []
            for c in candidates:
                title = c.get("title", "").lower()
                desc = c.get("description", "").lower()
                price = c.get("price")
                
                # Price boost
                if price is not None and price > 0:
                    c["similarity"] *= 1.15
                    print(f"   🛒 Price boost (×1.15): {title[:30]}...")
                
       
                
                # Shopping keywords boost
                shopping_keywords = ["buy", "price", "shop", "deal", "discount", "purchase", "shopping", "checkout", "cart", "$", "HK$", "US$"]
                if any(kw in (title + " " + desc) for kw in shopping_keywords):
                    c["similarity"] *= 1.05
                    print(f"   💰 Shopping keyword boost (×1.05): {title[:30]}...")
                
                # Brand match boost
                brand = user_input.get('brands', '')
                if brand and brand.lower() in (title + " " + desc):
                    c["similarity"] *= 1.10
                    print(f"   🏷️ Brand match boost (×1.10): {title[:30]}...")
                
                # Version match boost
                version = user_input.get('version', '')
                if version and version.lower() in (title + " " + desc):
                    c["similarity"] *= 1.15
                    print(f"   📌 Version match boost (×1.15): {title[:30]}...")

                # Also check for obsolete year in title/description
                combined_text = (title + " " + desc).lower()
                years_in_text = re.findall(r'20\d{2}', combined_text)
                is_obsolete = False
                for y_str in years_in_text:
                    y = int(y_str)
                    if y < current_year - 1:
                        is_obsolete = True
                        break
                if is_obsolete:
                    print(f"   ⛔ Skipping obsolete product (old year): {title[:40]}...")
                    continue
                else:
                    filtered_candidates.append(c)

            if filtered_candidates:
                candidates = filtered_candidates
                print(f"✅ After obsolete filter: {len(candidates)} candidates")
            else:
                print("⚠️ No non-obsolete products, keeping all")

            # ---------- E-commerce domain weights ----------
            ecommerce_weights = {
                # Hong Kong
                'price.com.hk': 0.12,
                'hk.shop': 0.10,
                'amazon.hk': 0.10,
                '.hk': 0.10,
                'fortress.com.hk': 0.12,
                'broadway.com.hk': 0.12,
                'suning.hk': 0.10,
                'cmhk.com': 0.08,
                '3hk.com': 0.08,
                'smartone.com': 0.08,

                # Taiwan
                'pchome': 0.12,
                'shopee.tw': 0.12,
                'momoshop': 0.12,
                'yahoo.com.tw': 0.10,
                '.com.tw': 0.08,
                '.tw': 0.08,
                'ruten.com.tw': 0.08,
                'etmall.com.tw': 0.10,
                'udn.com': 0.08,
                'myfone.com.tw': 0.08,
                'kbro.com.tw': 0.08,
                'feebee.com.tw': 0.08,

                # China
                'tmall': 0.12,
                'jd.com': 0.12,
                'taobao': 0.12,
                'suning': 0.10,
                'amazon.cn': 0.10,
                '.cn': 0.08,
                'gome.com.cn': 0.10,
                'dangdang.com': 0.08,
                'vip.com': 0.08,
                'xiaomi.com': 0.08,
                'honor.com': 0.08,
                'opposhop.com': 0.08,
                'vivo.com': 0.08,
                'meizu.com': 0.08,
                'oneplus.com': 0.08,
                'suning.com': 0.10,

                # United Kingdom
                'amazon.co.uk': 0.12,
                'ebay.co.uk': 0.10,
                'argos': 0.10,
                'currys': 0.10,
                'johnlewis': 0.10,
                'tesco': 0.08,
                '.co.uk': 0.08,
                'maplin.co.uk': 0.08,
                'cclonline.com': 0.08,
                'overclockers.co.uk': 0.08,
                'scan.co.uk': 0.08,
                'box.co.uk': 0.08,

                # International
                'amazon': 0.08,
                'ebay': 0.08,
                'bestbuy': 0.08,
                'walmart': 0.08,
                'newegg': 0.08,
                'asus.com': 0.10,
                'bhphotovideo': 0.08,
                '.com': 0.06,
                '.us': 0.08,
                'target.com': 0.08,
                'costco.com': 0.08,
                'microcenter.com': 0.08,
                'adorama.com': 0.08,
                'lenovo.com': 0.08,
                'dell.com': 0.08,
                'hp.com': 0.08,
                'samsung.com': 0.08,
                'apple.com': 0.08,
                'sony.com': 0.08,
                'lg.com': 0.08,
                'huawei.com': 0.08,
                'xioami.com': 0.08,
                'oppo.com': 0.08,
                'vivo.com': 0.08,
                'realme.com': 0.08,
                'oneplus.com': 0.08,
                'nothing.tech': 0.08,
                'google.com/store': 0.08,
                'microsoft.com': 0.08,
            }

            # Non-ecommerce domains (should not be classified as e-commerce)
            non_ecommerce_exclude = [
                'wikipedia', 'reddit', 'quora', 'forum', 'blog', 'news', 'guide',
                'how-to', 'support', 'faq', 'review', 'comment', 'discuss',
                'for-home', 'welcome', 'award', 'best seller'
            ]
            shopping_keywords_detection = ['buy', 'price', 'cart', 'checkout', 'shop now', 'deal', 'discount', 'purchase']

            for c in candidates:
                url = c.get('url', '').lower()
                title = c.get('title', '').lower()
                desc = c.get('description', '').lower()
                combined = url + ' ' + title + ' ' + desc

                category = 'unknown'
                bonus = 0.0
                penalty = 1.0

                # ----- 1. E-commerce detection -----
                if not any(kw in url for kw in non_ecommerce_exclude):
                    max_weight = 0.0
                    for kw, weight in ecommerce_weights.items():
                        if kw in url or kw in title:
                            if weight > max_weight:
                                max_weight = weight
                    if max_weight > 0:
                        if any(kw in combined for kw in shopping_keywords_detection):
                            max_weight += 0.02
                        if 'buy' in url or 'shop' in url or 'cart' in url:
                            max_weight += 0.02
                        # Apply weight only if shopping intent is detected
                        if has_shopping_intent:
                            bonus = max_weight
                        else:
                            bonus = max_weight * 0.2  # reduce weight when no shopping intent
                        category = 'ecommerce'

                # ----- 2. Homepage / landing page detection (only for e-commerce) -----
                is_homepage = False
                landing_page = False
                if category == 'ecommerce':
                    # (logic moved below)
                    pass

                # ----- 3. Other category detection (for all products) -----
                if category == 'ecommerce':
                    # Official spec pages (even if e-commerce, penalize)
                    if any(kw in url for kw in ['techspec', 'specifications', 'specs', '/specs/', 'tech-specs']) or any(domain in url for domain in ['phonespecs.net', 'en.kalvo.com', 'mobilesdetail.com', 'deviceatlas.com', 'phonescoop.com', 'gsmarena.com', 'gsm-arena.com']):
                        category = 'official_spec'
                        bonus = 0.05
                        penalty = 0.75
                    # Resale/second-hand
                    elif any(kw in url for kw in ['carousell', 'eBay', 'mercari', 'poshmark', 'depop', 'vinted', 'facebook marketplace', 'offerup']):
                        category = 'resale'
                        bonus = 0.04
                        penalty = 0.8
                    # Reviews/encyclopedias
                    elif any(kw in url for kw in ['cnet', 'ultrabookreview', 'review', 'digitaltrends', 'content', 'techradar', 'theverge', 'gizmodo', 'engadget', 'pcmag', 'wired', 'arstechnica', 'rtings', 'tomsguide', 'techspot', 'wikipedia', 'britannica', 'encyclopedia', 'mobile01', 'cool3c', 'eprice', 'sogi']):
                        category = 'review'
                        bonus = 0.03
                        penalty = 0.7
                    # Forums/Q&A
                    elif any(kw in url for kw in ['quora', 'reddit', 'stackexchange', 'yahoo', 'answers', 'forum', 'discuss', 'ptt.cc', 'dcard', 'support', 'faq', 'how-to', 'guide', 'manual']):
                        category = 'forum'
                        bonus = 0.0
                        penalty = 0.4

                # Check for explicit buy URL path
                if '/buy/' in url:
                    category = 'ecommerce'
                    bonus = 0.12   # higher weight
                    penalty = 1.0

                # Landing page / category page detection
                if any(kw in url for kw in ['/filter', '/all-series', '/category', '/series']):
                    landing_page = True

                strong_homepage_keywords = []
                if any(kw in title for kw in strong_homepage_keywords):
                    is_homepage = True
                has_country_code = re.search(r'^/[a-z]{2}/', url) is not None
                if '/for-home/' in url and not has_country_code:
                    is_homepage = True 
                # Exclude support/FAQ pages
                if '/support/' in url or '/faq/' in url:
                    is_homepage = False

                # Apply homepage/landing page penalty
                if landing_page or is_homepage:
                    category = 'landing_page' if landing_page else 'homepage'
                    bonus = bonus * 0.5
                    penalty = 0.85
                    print(f"   🏠/📋 Landing/homepage penalty: {title[:30]}...")
                    c['similarity'] = (c['similarity'] + bonus) * penalty
                    c['category'] = category
                    continue  # skip further classification

                # News / blog / press releases
                if any(kw in url for kw in ['/news/', '/content/', 'blog', 'Android Central', 'article', 'press', 'release', 'announces']) or any(domain in url for domain in ['wired.com', 'lifewire.com', 'androidcentral.com', 'sammobile.com', 'bestproducts.com', 'androidauthority.com']): 
                    category = 'news'
                    bonus = 0.0
                    penalty = 0.7

                # Unknown or low-quality sites get strong penalty
                if category == 'unknown' or any(domain in url for domain in ['phonemore.com', 'youtube.com']):
                    penalty = 0.4   # 60% penalty for unknown
                if any(domain in url for domain in ['amazon.com']):
                    penalty = 0.9   
                    # slight penalty to avoid over-domination and some products cannot be shipped / currently unavaliable
                    # Amazon pages may not show "cannot be shipped" due to anti‑scraping

                # Apply final category weight
                c['similarity'] = (c['similarity'] + bonus) * penalty
                c['category'] = category

            # ---------- 3. Sort by similarity ----------
            candidates.sort(key=lambda x: x["similarity"], reverse=True)

            # ---------- Parallel URL accessibility check ----------
            import concurrent.futures

            def check_url_accessibility(url: str) -> dict:
                """Check if a URL is accessible using HEAD request."""
                result = {"url": url, "blocked": False, "reason": ""}
                if not url:
                    result["blocked"] = True
                    result["reason"] = "No URL"
                    return result
                try:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept-Language": "en-US,en;q=0.9",
                    }
                    resp = requests.head(url, headers=headers, timeout=5, allow_redirects=True)
                    if resp.status_code in [403, 429, 503]:
                        result["blocked"] = True
                        result["reason"] = f"HTTP {resp.status_code}"
                    elif "cloudflare" in resp.text.lower() or "just a moment" in resp.text.lower():
                        result["blocked"] = True
                        result["reason"] = "Cloudflare challenge"
                    elif "access denied" in resp.text.lower():
                        result["blocked"] = True
                        result["reason"] = "Access Denied"
                except requests.exceptions.Timeout:
                    result["blocked"] = True
                    result["reason"] = "Timeout"
                except Exception as e:
                    result["blocked"] = True
                    result["reason"] = str(e)[:30]
                return result

            print(f"🔍 Parallel accessibility check for {len(candidates)} links...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                future_to_prod = {executor.submit(check_url_accessibility, prod.get("url", "")): prod for prod in candidates}
                for future in concurrent.futures.as_completed(future_to_prod):
                    prod = future_to_prod[future]
                    result = future.result()
                    if result["blocked"]:
                        prod["similarity"] *= 0.20
                        prod["blocked"] = True
                        prod["block_reason"] = result["reason"]
                        print(f"   🚫 {result['reason']}: {prod.get('title', '')[:30]}...")
                    else:
                        prod["blocked"] = False
                        prod["block_reason"] = ""

            # Re-sort after adjustments
            candidates.sort(key=lambda x: x["similarity"], reverse=True)
            top_candidates = candidates[:TOP_K]

            # ---------- Generate evidence sentences ----------
            if progress_callback:
                progress_callback(80, "Generating match reasons...")

            for prod in top_candidates:
                prod["evidence_sentence"] = self._generate_evidence_sentence(user_input, prod)

            # Serialize (convert numpy types to Python float, etc.)
            top_candidates_serializable = []
            for prod in top_candidates:
                prod_copy = prod.copy()
                if 'similarity' in prod_copy:
                    prod_copy['similarity'] = float(prod_copy['similarity'])
                prod_copy['blocked'] = prod.get('blocked', False)
                prod_copy['block_reason'] = prod.get('block_reason', '')
                prod_copy['category'] = prod.get('category', 'unknown')
                top_candidates_serializable.append(prod_copy)

            # Cache and cleanup
            cache_results(query_text, top_candidates_serializable)
            print(f"🔍 DEBUG: Raw user_input = {user_input}")
            print(f"🔍 DEBUG: Processed query_text = '{query_text}'")
            delete_old_entries()

            if progress_callback:
                progress_callback(100, "Done.")

            return {
                "recommendations": top_candidates_serializable,
                "total_found": len(candidates),
                "message": self._get_result_message(len(top_candidates_serializable), len(candidates)),
                "source": "web",
                "user_input": {
                    "device_type": user_input.get("device_type"),
                    "brands": user_input.get("brands"),
                    "color": user_input.get("color"),
                    "version": user_input.get("version"),
                    "others": user_input.get("others")
                }
            }

        except Exception as e:
            logging.error(f"Agent process_request crashed: {e}", exc_info=True)
            if progress_callback:
                progress_callback(100, f"Error: {str(e)}")
            return {
                "recommendations": [],
                "total_found": 0,
                "message": f"Internal error: {str(e)}",
                "source": "error",
                "user_input": {
                    "device_type": user_input.get("device_type"),
                    "brands": user_input.get("brands"),
                    "color": user_input.get("color"),
                    "version": user_input.get("version"),
                    "others": user_input.get("others")
                }
            }

    def _generate_template_evidence(self, user_input: dict, product: dict) -> str:
        """Fallback template-based evidence sentence (no API)."""
        device = user_input.get('device_type', '')
        brand = user_input.get('brands', '')
        version = user_input.get('version', '')
        color = user_input.get('color', '')
        others = user_input.get('others', '')
        title = product.get('title', '')

        parts = []
        if brand:
            parts.append(brand)
        if version:
            parts.append(version)
        if device:
            parts.append(device)
        if color:
            parts.append(color)
        if others:
            parts.append(others)
        desc = ' '.join(parts) if parts else 'this product'

        import random
        templates = [
            f"This {desc} is a great match for your needs, offering the features and performance you're looking for.",
            f"We found a {desc} that fits your preferences perfectly, with all the specifications you requested.",
            f"The {desc} aligns well with what you're looking for, delivering excellent value and quality.",
            f"Here's a {desc} that meets your criteria, combining reliability with the features you wanted.",
            f"Your search for {desc} returned this excellent option, which ticks all the right boxes.",
            f"This {desc} matches your request with its impressive design and the capabilities you specified.",
            f"We recommend this {desc} for your needs, as it offers the perfect balance of form and function."
        ]
        sentence = random.choice(templates)
        if len(sentence.split()) < 25:
            sentence = sentence.replace('.', ' — a solid choice for your needs.')
        return sentence

    def _generate_evidence_sentence(self, user_input: dict, product: dict) -> str:
        """
        Generate evidence sentence: prefer DeepSeek API, fallback to template.
        """
        if not DEEPSEEK_API_KEY:
            return self._generate_template_evidence(user_input, product)

        device = user_input.get('device_type', '')
        brand = user_input.get('brands', '')
        version = user_input.get('version', '')
        color = user_input.get('color', '')
        others = user_input.get('others', '')
        title = product.get('title', '')

        parts = []
        if brand:
            parts.append(brand)
        if version:
            parts.append(version)
        if device:
            parts.append(device)
        if color:
            parts.append(color)
        if others:
            parts.append(others)
        user_desc = ' '.join(parts) if parts else 'product'

        prompt = f"Product: {title[:120]}. User wants: {user_desc}. In one clear sentence (20-40 words), explain why this product matches."

        try:
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0.3,
            }
            print(f"🔍 Calling DeepSeek for evidence sentence...")
            resp = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                json=data,
                headers=headers,
                timeout=15
            )
            if resp.status_code == 200:
                result = resp.json()
                sentence = result["choices"][0]["message"].get("content", "").strip()
                if sentence:
                    return sentence
                else:
                    print("⚠️ DeepSeek returned empty content")
            else:
                print(f"⚠️ DeepSeek API error: {resp.status_code}")
        except Exception as e:
            print(f"⚠️ DeepSeek exception: {e}")

        return self._generate_template_evidence(user_input, product)

    def _get_result_message(self, top_count: int, total_count: int) -> str:
        if total_count == 0:
            return "No matching products found."
        elif total_count < TOP_K:
            return f"Found only {total_count} products. Showing all."
        elif top_count == 0:
            return "No products within your needs."
        else:
            return f"Top {top_count} recommendations out of {total_count} candidates."