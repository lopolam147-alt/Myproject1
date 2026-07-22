import spacy
import re

nlp = spacy.load("en_core_web_sm")

def clean_text(text: str) -> str:
    """Remove extra spaces, punctuation, and lowercase."""
    if not text:
        return ""
    # 確保傳入的是字串
    text = str(text)
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Remove punctuation (keep letters, digits, spaces)
    text = re.sub(r'[^\w\s]', '', text)
    return text.lower()

def extract_keywords(text: str) -> list[str]:
    """Extract lemmatized keywords using spaCy."""
    doc = nlp(text)
    keywords = [
        token.lemma_.lower()
        for token in doc
        # 只移除「停用詞」、「標點符號」和「空白」
        # 注意：我們移除了 token.is_alpha，這樣 "17pro" 就不會被丟掉！
        if not token.is_stop and not token.is_punct and not token.is_space
    ]
    return keywords

def build_query_from_input(data: dict) -> str:
    """
    將使用者輸入轉為搜尋字串。
    注意：price 和 country 不會被放入搜尋字串（country 僅供未來貨幣轉換用）。
    """
    parts = []
    
    # 定義哪些欄位「真正」要用來搜尋
    searchable_fields = ["device_type", "brands", "color", "version"]
    
    for field in searchable_fields:
        value = data.get(field)
        if value is None:
            continue
        
        # 安全處理數值（例如版本號若為數字）
        if isinstance(value, (int, float)):
            cleaned = str(value)
        else:
            cleaned = clean_text(str(value))
        
        if cleaned:
            # 保留欄位名稱前綴，讓 Tavily 更精準 (例如 brand:Apple)
            parts.append(f"{field}:{cleaned}")

    # 處理「Others」（額外關鍵字）
    if data.get("others"):
        others_cleaned = clean_text(data["others"])
        if others_cleaned:
            parts.append(others_cleaned)

    # 組合完整的查詢字串
    full_query = " ".join(parts)
    keywords = extract_keywords(full_query)
    
    return " ".join(keywords)

def normalize_synonyms(text: str) -> str:
    """將常見縮寫、同義詞、拼寫變體統一為標準形式，提升搜尋一致性。"""
    replacements = {
        # ----- 電視 / 顯示器 -----
        r'\btv\b': 'television',
        
        # ----- 手機 / 智慧型手機 -----
        r'\bphone\b': 'phone',
        r'\bmobile phone\b': 'phone',
        r'\bcellphone\b': 'phone',
        r'\bmobile\b': 'phone',
        r'\bsmartphone\b': 'phone',
        r'\bhandset\b': 'phone',
        
        # ----- 電腦 / 筆電 -----
        r'\blaptop\b': 'notebook computer',
        r'\bpc\b': 'computer',
        r'\bdesktop\b': 'desktop computer',
        r'\bnotebook\b': 'notebook computer',
        r'\bultrabook\b': 'notebook computer',
  
        # ----- 音響 / 耳機 -----
        r'\bheadphone\b': 'headphones',
        r'\bheadset\b': 'headphones',
        r'\bearphone\b': 'earphones',
        r'\bspeaker\b': 'speakers',
        r'\bsoundbar\b': 'soundbar',
        
        # ----- 家用電器 -----
        r'\bfridge\b': 'refrigerator',
        r'\bwasher\b': 'washing machine',
        r'\bdryer\b': 'clothes dryer',
        r'\bvacuum\b': 'vacuum cleaner',
        r'\brobot vacuum\b': 'robot vacuum',
        r'\bair purifier\b': 'air purifier',
  
        # ========== 國家 / 地區（已修正重複鍵問題） ==========
        r'\buk\b': 'united kingdom',
        r'\bgreat britain\b': 'united kingdom',   # ← 新增，標準化 Great Britain
        r'\busa\b': 'united states',
        r'\bus\b': 'united states',
        r'\bamerica\b': 'united states',          # ← 新增，標準化 America
        r'\bchina\b': 'china',
        r'\brepublic of china\b': 'china',        # ← 新增，標準化 Republic of China（有空格）
        r'\btw\b': 'taiwan',
        r'\bhk\b': 'hong kong',
        r'\bjp\b': 'japan',
        # ===================================================
        
        # ----- 通用規格 / 單位 -----
        r'\bram\b': 'memory',
        r'\bssd\b': 'solid state drive',
        r'\bhdd\b': 'hard disk drive',
        r'\buhd\b': 'ultra high definition',
        r'\boled\b': 'oled display',
        r'\blcd\b': 'lcd display',
        r'\bqled\b': 'qled display',
        
        # ----- 購物相關 -----
        r'\bbuy\b': 'purchase',
        r'\bshop\b': 'store',
        r'\bdeal\b': 'sale',
        r'\bdiscount\b': 'discount',
        r'\bpromo\b': 'promotion',
        
        # ----- 其他常見 -----
        r'\bvs\b': 'versus',
        r'\bwifi\b': 'wireless internet',
        r'\bbluetooth\b': 'bluetooth',
        r'\bgps\b': 'gps navigation',
        r'\busb\b': 'usb',
        r'\bhdmi\b': 'hdmi',
        r'\btype-c\b': 'usb-c',
    }
    
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text