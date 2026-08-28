"""
Utilities for text cleaning, keyword extraction, and query building.
Uses spaCy for lemmatization.
"""

import spacy
import re
from datetime import datetime

nlp = spacy.load("en_core_web_sm")

def clean_text(text: str) -> str:
    """Remove extra spaces, punctuation, and lowercase."""
    if not text:
        return ""
    text = str(text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[^\w\s]', '', text)
    return text.lower()

def extract_keywords(text: str) -> list[str]:
    """Extract lemmatized keywords using spaCy."""
    doc = nlp(text)
    keywords = [
        token.lemma_.lower()
        for token in doc
        if not token.is_stop and not token.is_punct and not token.is_space
    ]
    return keywords

def build_query_from_input(data: dict) -> str:
    """
    Build a search query string from user input.
    Fields: device_type, brands, color, version, others.
    (No price/country handling.)
    """
    parts = []
    searchable_fields = ["device_type", "brands", "color", "version", "others"]

    for field in searchable_fields:
        value = data.get(field)
        if value is None:
            continue

        if isinstance(value, (int, float)):
            cleaned = str(value)
        else:
            cleaned = clean_text(str(value))

        if cleaned:
            # Skip color if it's empty or "any"/"blank"
            if field == "color" and cleaned in ("", "any", "blank"):
                continue
            # Add prefix for better search engine understanding
            parts.append(f"{field}:{cleaned}")

    full_query = " ".join(parts)
    keywords = extract_keywords(full_query)
    return " ".join(keywords)

def normalize_synonyms(text: str) -> str:
    """
    Normalize common abbreviations, synonyms, and spelling variants for consistency.
    (Currently not used in the pipeline, but kept for reference.)
    """
    replacements = {
        # TV / Display
        r'\btv\b': 'television',
        
        # Phones / Smartphones
        r'\bphone\b': 'phone',
        r'\bmobile phone\b': 'phone',
        r'\bcellphone\b': 'phone',
        r'\bmobile\b': 'phone',
        r'\bsmartphone\b': 'phone',
        r'\bhandset\b': 'phone',
        
        # Computers / Laptops
        r'\blaptop\b': 'notebook computer',
        r'\bpc\b': 'computer',
        r'\bdesktop\b': 'desktop computer',
        r'\bnotebook\b': 'notebook computer',
        r'\bultrabook\b': 'notebook computer',
  
        # Audio / Headphones
        r'\bheadphone\b': 'headphones',
        r'\bheadset\b': 'headphones',
        r'\bearphone\b': 'earphones',
        r'\bspeaker\b': 'speakers',
        r'\bsoundbar\b': 'soundbar',
        
        # Home appliances
        r'\bfridge\b': 'refrigerator',
        r'\bwasher\b': 'washing machine',
        r'\bdryer\b': 'clothes dryer',
        r'\bvacuum\b': 'vacuum cleaner',
        r'\brobot vacuum\b': 'robot vacuum',
        r'\bair purifier\b': 'air purifier',
  
        # Country / Region
        r'\buk\b': 'united kingdom',
        r'\bgreat britain\b': 'united kingdom',
        r'\busa\b': 'united states',
        r'\bus\b': 'united states',
        r'\bamerica\b': 'united states',
        r'\bchina\b': 'china',
        r'\brepublic of china\b': 'china',
        r'\btw\b': 'taiwan',
        r'\bhk\b': 'hong kong',
        r'\bjp\b': 'japan',
        
        # Specs / Units
        r'\bram\b': 'memory',
        r'\bssd\b': 'solid state drive',
        r'\bhdd\b': 'hard disk drive',
        r'\buhd\b': 'ultra high definition',
        r'\boled\b': 'oled display',
        r'\blcd\b': 'lcd display',
        r'\bqled\b': 'qled display',
        
        # Shopping
        r'\bbuy\b': 'purchase',
        r'\bshop\b': 'store',
        r'\bdeal\b': 'sale',
        r'\bdiscount\b': 'discount',
        r'\bpromo\b': 'promotion',
        
        # Others
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