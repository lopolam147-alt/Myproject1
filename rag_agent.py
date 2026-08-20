from math import prod
from currency_utils import detect_currency_from_url
from utils import build_query_from_input, extract_keywords
from database import get_cached_results, cache_results, delete_old_entries
from search_engine import fetch_products
from embedder import get_embedding, cosine_similarity
import os
import json
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
                        "user_input": {  # ✅ 添加
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
            print(f"🔍 提取的關鍵詞: {keywords}")

            candidates = fetch_products(
                query_text,
                max_candidates=MAX_CANDIDATES,
                progress_callback=progress_callback,
                keywords=keywords   # ✅ 传递 keywords
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

            # ---------- 1. 先過濾「沒有價格」的商品（確保可購買） ----------
            print(f"🛒 過濾前共有 {len(candidates)} 個候選商品")
            candidates_with_price = []
            for c in candidates:
                price = c.get("price")
                if price is None or price <= 0:
                    c["no_price"] = True
                    c["similarity"] *= 0.8  # 无价格降权 20%
                    print(f"   ⚠️ 無價格降權 (×0.8): {c.get('title', '')[:40]}...")
                else:
                    c["no_price"] = False

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
                title = c.get("title", "").lower()
                desc = c.get("description", "").lower()
                price = c.get("price")
                
                # 1️⃣ 有价格 → 加權 15%
                if price is not None and price > 0:
                    c["similarity"] *= 1.15
                    print(f"   🛒 有價格加權 (×1.15): {title[:30]}...")
                
                # 不可配送降權
                if c.get('unavailable', False):
                    c["similarity"] *= 0.75
                    print(f"   🚫 不可配送降權 (×0.75): {title[:30]}...")
                
                # 3️⃣ 購物關鍵字加分
                shopping_keywords = ["buy", "price", "shop", "deal", "discount", "purchase", "shopping", "checkout", "cart", "$", "HK$", "US$"]
                if any(kw in (title + " " + desc) for kw in shopping_keywords):
                    c["similarity"] *= 1.05
                    print(f"   💰 購物關鍵字加分 (×1.05): {title[:30]}...")
                
                # 🆕 品牌加权
                brand = user_input.get('brands', '')
                if brand and brand.lower() in (title + " " + desc):
                    c["similarity"] *= 1.10
                    print(f"   🏷️ 品牌匹配加權 (×1.10): {title[:30]}...")
                
                # 🆕 版本加权
                version = user_input.get('version', '')
                if version and version.lower() in (title + " " + desc):
                    c["similarity"] *= 1.15
                    print(f"   📌 版本匹配加權 (×1.15): {title[:30]}...")

            # ---------- 电商/平台权重（使用用户提供的具体权重） ----------
            # 权重越高，越优先
            ecommerce_weights = {
                # ========== 香港 ==========
                'price.com.hk': 0.12,
                'hk.shop': 0.10,
                'amazon.hk': 0.10,
                '.hk': 0.10,
                'fortress.com.hk': 0.12,          # 豐澤
                'broadway.com.hk': 0.12,          # 百老匯
                'suning.hk': 0.10,                # 蘇寧香港
                'cmhk.com': 0.08,                 # 中國移動香港 (手機)
                '3hk.com': 0.08,                  # 和記電訊
                'smartone.com': 0.08,             # 數碼通

                # ========== 台灣 ==========
                'pchome': 0.12,
                'shopee.tw': 0.12,
                'momoshop': 0.12,
                'yahoo.com.tw': 0.10,
                '.com.tw': 0.08,
                '.tw': 0.08,
                'ruten.com.tw': 0.08,             # 露天拍賣
                'etmall.com.tw': 0.10,            # 東森購物
                'udn.com': 0.08,                  # 聯合新聞網購物
                'myfone.com.tw': 0.08,            # 台灣大哥大 myfone
                'kbro.com.tw': 0.08,              # 凱擘大寬頻
                'feebee.com.tw': 0.08,            # 飛比價格

                # ========== 中國 ==========
                'tmall': 0.12,
                'jd.com': 0.12,
                'taobao': 0.12,
                'suning': 0.10,
                'amazon.cn': 0.10,
                '.cn': 0.08,
                'gome.com.cn': 0.10,              # 國美電器
                'dangdang.com': 0.08,             # 當當網
                'vip.com': 0.08,                  # 唯品會
                'xiaomi.com': 0.08,               # 小米商城
                'honor.com': 0.08,                # 榮耀商城
                'opposhop.com': 0.08,             # OPPO 商城
                'vivo.com': 0.08,                 # vivo 商城
                'meizu.com': 0.08,                # 魅族商城
                'oneplus.com': 0.08,              # 一加商城
                'suning.com': 0.10,               # 蘇寧易購

                # ========== 英國 ==========
                'amazon.co.uk': 0.12,
                'ebay.co.uk': 0.10,
                'argos': 0.10,
                'currys': 0.10,
                'johnlewis': 0.10,
                'tesco': 0.08,
                '.co.uk': 0.08,
                'maplin.co.uk': 0.08,             # 電子零件連鎖
                'cclonline.com': 0.08,            # PC 零件
                'overclockers.co.uk': 0.08,       # DIY PC
                'scan.co.uk': 0.08,               # 電腦硬體
                'box.co.uk': 0.08,                # 電子產品

                # ========== 通用（國際） ==========
                'amazon': 0.08,
                'ebay': 0.08,
                'bestbuy': 0.08,
                'walmart': 0.08,
                'newegg': 0.08,
                'asus.com': 0.10,
                'bhphotovideo': 0.08,
                '.com': 0.06,
                '.us': 0.08,
                'target.com': 0.08,               # 美國 Target
                'costco.com': 0.08,               # 美國 Costco
                'microcenter.com': 0.08,          # 美國電腦專賣
                'adorama.com': 0.08,              # 美國攝影/電子
                'lenovo.com': 0.08,               # Lenovo 官網
                'dell.com': 0.08,                 # Dell 官網
                'hp.com': 0.08,                   # HP 官網
                'samsung.com': 0.08,              # Samsung 官網
                'apple.com': 0.08,                # Apple 官網
                'sony.com': 0.08,                 # Sony 官網
                'lg.com': 0.08,                   # LG 官網
                'huawei.com': 0.08,               # 華為官網
                'xioami.com': 0.08,               # 小米官網
                'oppo.com': 0.08,                 # OPPO 官網
                'vivo.com': 0.08,                 # vivo 官網
                'realme.com': 0.08,               # realme 官網
                'oneplus.com': 0.08,              # 一加官網
                'nothing.tech': 0.08,             # Nothing 官網
                'google.com/store': 0.08,         # Google 商店
                'microsoft.com': 0.08,            # Microsoft 官網
            }

            # 非电商排除词（这些即使包含 .com 也不应被视为电商）
            non_ecommerce_exclude = [
                'wikipedia', 'reddit', 'quora', 'forum', 'blog', 'news', 'guide',
                'how-to', 'support', 'faq', 'review', 'comment', 'discuss',
                'for-home', 'welcome', 'award', 'best seller'
            ]
            # 购物关键词（用于补充判断）
            shopping_keywords_detection = ['buy', 'price', 'cart', 'checkout', 'shop now', 'deal', 'discount', 'purchase']

            for c in candidates:
                url = c.get('url', '').lower()
                title = c.get('title', '').lower()
                desc = c.get('description', '').lower()
                combined = url + ' ' + title + ' ' + desc

                category = 'unknown'
                bonus = 0.0
                penalty = 1.0  # ✅ 改为 1.0

                # ----- 1. 电商权重计算 -----
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
                        bonus = max_weight
                        category = 'ecommerce'


                # ----- 2. 首页/列表页检测（仅针对电商分类） -----
                is_homepage = False
                landing_page = False
                if category == 'ecommerce':
                    # ... 首页检测逻辑 ...
                    pass

                # ----- 3. 其他分类检测（对所有商品执行） -----
                if category == 'ecommerce':
                    # 官方规格页
                    if any(kw in url for kw in ['techspec', 'specifications', 'specs', '/specs/', 'tech-specs']) or any(domain in url for domain in ['phonespecs.net', 'deviceatlas.com', 'phonescoop.com', 'gsmarena.com']):
                        category = 'official_spec'
                        bonus = 0.06
                        penalty = 0.8
                    # 二手/转售
                    elif any(kw in url for kw in ['carousell', 'eBay', 'mercari', 'poshmark', 'depop', 'vinted', 'facebook marketplace', 'offerup']):
                        category = 'resale'
                        bonus = 0.04
                        penalty = 0.9
                    # ... 其他分类 ...
                else:
                    # 如果未判定为电商，也检测其他分类
                    # 二手/转售
                    if any(kw in url for kw in ['carousell', 'eBay', 'mercari', 'poshmark', 'depop', 'vinted', 'facebook marketplace', 'offerup']):
                        category = 'resale'
                        bonus = 0.04
                        penalty = 0.9
                    # 评测/百科
                    elif any(kw in url for kw in ['cnet', 'ultrabookreview', 'review', 'digitaltrends', 'content', 'techradar', 'theverge', 'gizmodo', 'engadget', 'pcmag', 'wired', 'arstechnica', 'rtings', 'tomsguide', 'techspot', 'wikipedia', 'britannica', 'encyclopedia', 'mobile01', 'cool3c', 'eprice', 'sogi']):
                        category = 'review'
                        bonus = 0.03
                        penalty = 0.7
                    # 论坛/问答
                    elif any(kw in url for kw in ['quora', 'reddit', 'stackexchange', 'yahoo', 'answers', 'forum', 'discuss', 'ptt.cc', 'dcard', 'support', 'faq', 'how-to', 'guide', 'manual']):
                        category = 'forum'
                        bonus = 0.0
                        penalty = 0.4

                    
                # 列表页/筛选页特征（无论是否为电商都检测）
                if any(kw in url for kw in ['/filter', '/all-series', '/category', '/series']):
                    landing_page = True

                # 应用首页/列表页降权
                if landing_page or is_homepage:
                    category = 'landing_page' if landing_page else 'homepage'
                    bonus = bonus * 0.5
                    penalty = 0.85
                    print(f"   🏠/📋 降权: {title[:30]}...")
                    # ⚠️ 注意：如果判定为首页/列表页，跳过后续其他分类检测
                    # 直接跳到应用权重
                    c['similarity'] = (c['similarity'] + bonus) * penalty
                    c['category'] = category
                    continue  # 跳过后续分类检测
                if any(kw in url for kw in ['/news/', '/content/', 'blog', 'Android Central', 'article', 'press', 'release', 'announces']) or any(domain in url for domain in ['wired.com', 'lifewire.com', 'androidcentral.com', 'sammobile.com', 'bestproducts.com', 'androidauthority.com']): 
                    category = 'news'
                    bonus = 0.0
                    penalty = 0.7
                if category == 'unknown' or any(domain in url for domain in ['phonemore.com']):
                    penalty = 0.4   # 未知分类降权 60%
                if any(domain in url for domain in ['amazon.com']):
                    penalty = 0.9
                strong_homepage_keywords = []
                if any(kw in title for kw in strong_homepage_keywords):
                    is_homepage = True
                has_country_code = re.search(r'^/[a-z]{2}/', url) is not None
                if '/for-home/' in url and not has_country_code:
                    is_homepage = True 
                # 排除支持/帮助页面
                if '/support/' in url or '/faq/' in url:
                    is_homepage = False
                
                # ----- 5. 应用权重 -----
                c['similarity'] = (c['similarity'] + bonus) * penalty
                c['category'] = category
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
            top_candidates = candidates[:TOP_K]

            if progress_callback:
                progress_callback(80, "Generating match reasons...")

            for prod in top_candidates:
                prod["evidence_sentence"] = self._generate_evidence_sentence(user_input, prod) 

            # --- Convert numpy float32 to Python float before caching ---
            top_candidates_serializable = []
            for prod in top_candidates:
                prod_copy = prod.copy()
                if 'similarity' in prod_copy:
                    prod_copy['similarity'] = float(prod_copy['similarity'])
                prod_copy['blocked'] = prod.get('blocked', False)
                prod_copy['block_reason'] = prod.get('block_reason', '')
                prod_copy['category'] = prod.get('category', 'unknown')
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
                "source": "web",
                "user_input": {  # ✅ 添加
                    "device_type": user_input.get("device_type"),
                    "brands": user_input.get("brands"),
                    "color": user_input.get("color"),
                    "version": user_input.get("version"),
                    "others": user_input.get("others")
                }
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
                "source": "error",
                "user_input": {  # ✅ 可选，也可以不加
                    "device_type": user_input.get("device_type"),
                    "brands": user_input.get("brands"),
                    "color": user_input.get("color"),
                    "version": user_input.get("version"),
                    "others": user_input.get("others")
                }
            }
        
    def _generate_template_evidence(self, user_input: dict, product: dict) -> str:
        """更自然的本地模板（无需 API），25-50 词"""
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
        # 确保句子长度在 25-50 词之间，如果太短则拼装
        if len(sentence.split()) < 20:
            sentence = sentence.replace('.', ' — a solid choice for your needs.')
        return sentence

    def _generate_evidence_sentence(self, user_input: dict, product: dict) -> str:
        """
        生成证据句：优先调用 DeepSeek API，失败回退到本地模板。
        绝不显示推理内容。
        """
        # 如果未设置 API Key，直接使用模板
        if not DEEPSEEK_API_KEY:
            return self._generate_template_evidence(user_input, product)

        device = user_input.get('device_type', '')
        brand = user_input.get('brands', '')
        version = user_input.get('version', '')
        color = user_input.get('color', '')
        others = user_input.get('others', '')
        title = product.get('title', '')

        # 构建用户关键词描述
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

        # 精简 prompt，明确要求直接输出答案
        prompt = f"Product: {title[:120]}. User wants: {user_desc}. In one clear sentence (20-40 words), explain why this product matches."

        try:
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 100,        # 确保足够
                "temperature": 0.3,
                # 如果模型支持，减少推理 token
                # "reasoning_effort": "low"
            }
            resp = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                json=data,
                headers=headers,
                timeout=15
            )
            if resp.status_code == 200:
                result = resp.json()
                # 只取 content，忽略 reasoning_content
                sentence = result["choices"][0]["message"].get("content", "").strip()
                if sentence:
                    # 如果句子太长或太短，可以修剪，但这里直接返回
                    return sentence
                else:
                    print("⚠️ DeepSeek 返回空 content")
            else:
                print(f"⚠️ DeepSeek API 错误: {resp.status_code}")
        except Exception as e:
            print(f"⚠️ DeepSeek 调用异常: {e}")

        # 如果 API 失败，使用本地模板
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

    