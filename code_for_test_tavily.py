from tavily import TavilyClient
import os

client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
try:
    resp = client.search(query="laptop", max_results=3, timeout=30)
    print("✅ Tavily is working normally：", len(resp.get("results", [])))
except Exception as e:
    print("❌ Tavily error：", e)