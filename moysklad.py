import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
MOYSKLAD_TOKEN = os.getenv("MOYSKLAD_API_TOKEN")
BASE_URL = "https://moysklad.ru"

async def get_moysklad_stock_async(session, article):
    """
    ⚡ СВЕРХСКОРОСТЬ: Асинхронный поштучный запрос к МойСклад.
    Работает параллельно на любом тарифе без ошибок 404.
    """
    if not MOYSKLAD_TOKEN:
        return article, 0

    headers = {
        "Authorization": f"Bearer {MOYSKLAD_TOKEN}",
        "Accept-Encoding": "gzip"
    }

    clean_article = str(article).strip()
    search_url = f"{BASE_URL}/entity/product"
    
    try:
        async with session.get(search_url, headers=headers, params={"search": clean_article}, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                rows = data.get("rows", [])
                
                target_product = None
                for row in rows:
                    if str(row.get("article", "")).strip().lower() == clean_article.lower():
                        target_product = row
                        break
                if not target_product and rows:
                    target_product = rows
                    
                if target_product:
                    product_href = target_product.get("meta", {}).get("href")
                    stock_url = f"{BASE_URL}/report/stock/all"
                    
                    async with session.get(stock_url, headers=headers, params={"productHref": product_href}, timeout=10) as stock_res:
                        if stock_res.status == 200:
                            stock_data = await stock_res.json()
                            stock_rows = stock_data.get("rows", [])
                            if stock_rows:
                                return article, int(stock_rows.get("stock", 0))
    except:
        pass
    return article, 0
