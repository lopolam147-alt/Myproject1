from math import prod

from currency_utils import detect_currency_from_url
import currency_utils
from utils import build_query_from_input, extract_keywords
from database import get_cached_results, cache_results, delete_old_entries
from search_engine import fetch_products
from embedder import get_embedding, cosine_similarity
from currency_utils import detect_currency_from_url, get_target_currency_from_country, convert_price
import os
import json
from datetime import datetime
import logging
import requests
import re

logging.basicConfig(level=logging.INFO)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", 30))
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
            cached = get_cached_results(query_text)
            if cached is not None:
                if progress_callback:
                    progress_callback(100, "Returning cached results.")
                return {"recommendations": cached, "source": "cache"}

            if progress_callback:
                progress_callback(40, f"Searching web for '{query_text}'...")
            candidates = fetch_products(query_text, max_candidates=MAX_CANDIDATES, progress_callback=progress_callback)
            
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

            # ---------- 1. 先過濾「沒有價格」的商品（確保可購買） ----------
            print(f"🛒 過濾前共有 {len(candidates)} 個候選商品")
            candidates_with_price = []
            for c in candidates:
                price = c.get("price")
                # 只要有價格（且大於 0），就保留
                if price is not None and price > 0:
                    candidates_with_price.append(c)
                else:
                    title = c.get("title", "")
                    print(f"   ⛔ 排除無價格商品: {title[:40]}...")

            # 如果有「含價格」的商品，就只保留這些；否則（真的很罕見）才保留所有商品以防完全沒結果
            if candidates_with_price:
                candidates = candidates_with_price
                print(f"✅ 保留 {len(candidates)} 個有標示價格的商品")
            else:
                print("⚠️ 完全沒有找到有價格的商品，暫時放寬限制顯示所有結果（可能包含指南）")

            # ---------- 2. 過濾「明顯過時」的商品（例如標題寫 2020） ----------
            current_year = datetime.now().year
            filtered_candidates = []
            for c in candidates:
                title = c.get("title", "")
                desc = c.get("description", "")
                combined_text = (title + " " + desc).lower()
                
                # 找出所有年份（例如 2020, 2021, 2022...）
                years_in_text = re.findall(r'20\d{2}', combined_text)
                is_obsolete = False
                for y_str in years_in_text:
                    y = int(y_str)
                    # 如果年份小於「去年」，就認定為過時（例如 2024 年中的時候，2023 可能還勉強，但 2022 以前就算過時）
                    if y < current_year - 1:
                        is_obsolete = True
                        break
                
                if is_obsolete:
                    print(f"   ⛔ 排除過時商品: {title[:40]}... (提及舊年份)")
                else:
                    filtered_candidates.append(c)

            if filtered_candidates:
                candidates = filtered_candidates
                print(f"✅ 過濾過時商品後，剩餘 {len(candidates)} 個")
            for c in candidates:
                title = c.get("title", "").lower()
                url = c.get("url", "").lower()
                desc = c.get("description", "").lower()
                price = c.get("price")

                # 1️⃣ 有價格 → 加權 15%（讓商品頁面往上衝）
                if price is not None and price > 0:
                    c["similarity"] = c["similarity"] * 1.15
                    print(f"   🛒 商品加權: {title[:30]}... (新相似度: {c['similarity']:.2f})")

                # 2️⃣ 分級懲罰（依照網站類型）
                # 等級 1：輕微懲罰（科技新聞 / 專業媒體，降為 85%）
                tech_news_domains = ["cnet.com", "techradar.com", "theverge.com", "gizmodo.com", "engadget.com", "pcmag.com", "theguardian.com", "wired.com", "arstechnica.com"]
                # 等級 2：中度懲罰（專業評測 / 百科全書，降為 70%）
                review_domains = ["rtings.com", "wikipedia.org", "mobile01.com", "cool3c.com", "eprice.com.tw", "sogi.com.tw", "tomsguide.com", "techspot.com"]
                # 等級 3：嚴厲懲罰（問答 / 論壇 / 社群，降為 40%）
                qa_domains = ["quora.com", "reddit.com", "stackexchange.com", "yahoo.com", "answers.com", "forum", "discuss", "ptt.cc", "dcard.tw"]

                if any(d in url for d in tech_news_domains):
                    c["similarity"] = c["similarity"] * 0.85
                    print(f"   📰 科技新聞懲罰 (85%): {title[:30]}... (新相似度: {c['similarity']:.2f})")
                elif any(d in url for d in review_domains):
                    c["similarity"] = c["similarity"] * 0.70
                    print(f"   📚 評測/百科懲罰 (70%): {title[:30]}... (新相似度: {c['similarity']:.2f})")
                elif any(d in url for d in qa_domains):
                    c["similarity"] = c["similarity"] * 0.40
                    print(f"   ❓ 問答/論壇嚴懲 (40%): {title[:30]}... (新相似度: {c['similarity']:.2f})")

                # 3️⃣ 購物關鍵字加分（所有網站都適用）
                shopping_keywords = ["buy", "price", "shop", "deal", "discount", "purchase", "shopping", "checkout", "cart"]
                if any(kw in (title + " " + desc) for kw in shopping_keywords):
                    c["similarity"] = c["similarity"] * 1.05
                    print(f"   💰 購物關鍵字加分: {title[:30]}... (新相似度: {c['similarity']:.2f})")

            # ---------- 3. 最後再進行排序 ----------
            candidates.sort(key=lambda x: x["similarity"], reverse=True)
            # ========== 🚀 並行偵測連結可訪問性（針對所有候選商品） ==========
            import concurrent.futures

            def check_url_accessibility(url: str) -> dict:
                """檢查單一 URL，回傳狀態"""
                result = {"url": url, "blocked": False, "reason": ""}
                if not url:
                    result["blocked"] = True
                    result["reason"] = "無 URL"
                    return result
                try:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept-Language": "en-US,en;q=0.9",
                    }
                    # 使用 HEAD 請求，快速檢查
                    resp = requests.head(url, headers=headers, timeout=5, allow_redirects=True)
                    if resp.status_code in [403, 429, 503]:
                        result["blocked"] = True
                        result["reason"] = f"HTTP {resp.status_code}"
                    # 簡單檢查回應內容（若回應有 Cloudflare 特徵）
                    elif "cloudflare" in resp.text.lower() or "just a moment" in resp.text.lower():
                        result["blocked"] = True
                        result["reason"] = "Cloudflare 驗證"
                    elif "access denied" in resp.text.lower():
                        result["blocked"] = True
                        result["reason"] = "Access Denied"
                except requests.exceptions.Timeout:
                    result["blocked"] = True
                    result["reason"] = "連線逾時"
                except Exception as e:
                    result["blocked"] = True
                    result["reason"] = str(e)[:30]
                return result

            print(f"🔍 正在並行偵測 {len(candidates)} 個連結的可訪問性...")
            # 使用 ThreadPoolExecutor 並行檢查（最多 10 個執行緒）
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                # 提交所有任務
                future_to_prod = {executor.submit(check_url_accessibility, prod.get("url", "")): prod for prod in candidates}
                for future in concurrent.futures.as_completed(future_to_prod):
                    prod = future_to_prod[future]
                    result = future.result()
                    if result["blocked"]:
                        # 若被封鎖，極度嚴厲懲罰（分數 × 0.20）
                        prod["similarity"] = prod["similarity"] * 0.20
                        prod["blocked"] = True
                        prod["block_reason"] = result["reason"]
                        print(f"   🚫 {result['reason']}: {prod.get('title', '')[:30]}...")
                    else:
                        prod["blocked"] = False
                        prod["block_reason"] = ""

            # 重新排序（因為分數已被調整）
            candidates.sort(key=lambda x: x["similarity"], reverse=True)
            # =============================================================
            price_limit = user_input.get("price")
            if price_limit:
                try:
                    price_limit = float(price_limit)
                    # 1. 決定「使用者想要的幣別」（根據他填的 Country）
                    target_currency = get_target_currency_from_country(user_input.get("country", "us"))
                    print(f"🔄 目標幣別: {target_currency}, 使用者價格上限: {price_limit} {target_currency}")

                    filtered = []
                    for p in candidates:
                        original_price = p.get("price")
                        if original_price is None:
                            continue  # 沒價格的商品跳過過濾（保留它們，但不參與價格過濾？實務上我們直接跳過）
                        
                        # 2. 判斷該商品網頁的幣別（從 URL）
                        url = p.get("url", "")
                        from_currency = detect_currency_from_url(url)
                        
                        # 3. 轉換為使用者目標幣別
                        converted_price = convert_price(original_price, from_currency, target_currency)
                        
                        if converted_price is not None and converted_price <= price_limit:
                            # 把轉換後的價格存回 product，方便顯示
                            p["price"] = round(converted_price, 2)
                            p["currency"] = target_currency
                            filtered.append(p)
                        else:
                            print(f"   ⛔ 排除商品 {p.get('title', '')[:30]}... (原價 {original_price} {from_currency} -> {converted_price} {target_currency} 超出預算)")

                    if filtered:
                        candidates = filtered
                        print(f"✅ 過濾後剩餘 {len(candidates)} 個符合預算的商品")
                    else:
                        print(f"⚠️ 沒有任何商品符合 {price_limit} {target_currency} 的預算限制，改為顯示全部商品（保留價格資訊）")
                        # 為了不讓使用者看到空畫面，我們保留所有商品，但標註原始幣別
                        for p in candidates:
                            url = p.get("url", "")
                            from_currency = detect_currency_from_url(url)
                            p["currency"] = from_currency
                except Exception as e:
                    print(f"⚠️ 價格轉換失敗: {e}，跳過過濾")

            top_candidates = candidates[:TOP_K]

            if progress_callback:
                progress_callback(80, "Generating match reasons...")

            for prod in top_candidates:
                reason = self._generate_match_reason(user_input, prod)
                prod["match_reason"] = reason

            # --- Convert numpy float32 to Python float before caching ---
            top_candidates_serializable = []
            for prod in top_candidates:
                prod_copy = prod.copy()
                if 'similarity' in prod_copy:
                    prod_copy['similarity'] = float(prod_copy['similarity'])
                if 'price' in prod_copy and prod_copy['price'] is not None:
                    prod_copy['price'] = float(prod_copy['price'])
                prod_copy['blocked'] = prod.get('blocked', False)
                prod_copy['block_reason'] = prod.get('block_reason', '')
                top_candidates_serializable.append(prod_copy)

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
                "source": "web"
            }
        except Exception as e:
            import logging
            logging.error(f"Agent process_request crashed: {e}", exc_info=True)
            if progress_callback:
                progress_callback(100, f"Error: {str(e)}")
            return {
                "recommendations": [],
                "total_found": 0,
                "message": f"Internal error: {str(e)}",
                "source": "error"
            }

    def _generate_match_reason(self, user_input: dict, product: dict) -> str:
        """Use Deepseek API to generate a human-readable reason."""
        if not DEEPSEEK_API_KEY:
            # Fallback: simple template
            return f"Matches your {user_input.get('device_type', 'device')} criteria."
        try:
            prompt = f"""Given the user's request: {user_input}
            And the product: {product}
            Provide a brief reason (1 sentence) why this product matches the user's needs.
            """
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 50,
                "temperature": 0.3
            }
            resp = requests.post("https://api.deepseek.com/v1/chat/completions", json=data, headers=headers, timeout=10)
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
            else:
                logging.warning(f"Deepseek API error: {resp.text}")
                return "Matches your criteria."
        except Exception as e:
            logging.error(f"Deepseek reasoning failed: {e}")
            return "Matches your criteria."

    def _get_result_message(self, top_count: int, total_count: int) -> str:
        if total_count == 0:
            return "No matching products found."
        elif total_count < TOP_K:
            return f"Found only {total_count} products. Showing all."
        elif top_count == 0:
            return "No products within your price range."
        else:
            return f"Top {top_count} recommendations out of {total_count} candidates."