from pydantic import BaseModel
from typing import Optional

class UserInput(BaseModel):
    device_name: Optional[str] = "laptop"
    brands: Optional[str] = ""
    device_type: Optional[str] = ""
    color: Optional[str] = ""
    version: Optional[str] = ""
    price: Optional[float] = None
    country: Optional[str] = ""
    others: Optional[str] = ""

class ProductRecommendation(BaseModel):
    title: str
    description: str
    price: Optional[float]
    url: str
    similarity: float
    match_reason: str

class RecommendationResponse(BaseModel):
    recommendations: list[ProductRecommendation]
    total_found: int
    message: str
    source: str  # "cache" or "web"