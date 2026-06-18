import requests
from urllib.parse import urlparse, urljoin
from app.crawler.anti_blocking import get_random_headers
from app.crawler.extractor import clean_price_string, calculate_data_quality_score
from app.matching.model_extractor import extract_model
import logging

logger = logging.getLogger(__name__)

# Common APIs/JSON arrays endpoints
API_PROBING_PATHS = [
    "/api/products",
    "/products.json",
    "/wp-json/wc/v3/products",
    "/wp-json/wc/store/v1/products",
    "/products",
    "/search",
    "/ajax/products"
]

def discover_and_fetch_api(url: str) -> dict:
    """
    API Discovery Mode:
    Tries probing API endpoints on the target website to retrieve structured JSON products directly.
    """
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    
    headers = get_random_headers()
    headers["Accept"] = "application/json"
    
    # Try common API endpoints
    for path in API_PROBING_PATHS:
        target_api = urljoin(base_url, path)
        try:
            response = requests.get(target_api, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                product = extract_from_json_payload(data, url)
                if product:
                    return {"status": "success", "product": product}
        except Exception:
            continue
            
    return {"status": "failed", "reason": "No API/JSON source discovered"}

def extract_from_json_payload(data, url: str) -> dict:
    """
    Iterates over JSON payload looking for product configurations or product arrays.
    """
    # 1. Shopify / WooCommerce pattern (a list of products)
    products_list = []
    if isinstance(data, dict):
        if "products" in data and isinstance(data["products"], list):
            products_list = data["products"]
        elif "items" in data and isinstance(data["items"], list):
            products_list = data["items"]
    elif isinstance(data, list):
        products_list = data
        
    for item in products_list:
        if not isinstance(item, dict):
            continue
            
        # Match keys (Shopify, WC, custom API)
        name = item.get("title") or item.get("name") or ""
        sku = item.get("sku") or item.get("sku_code") or ""
        desc = item.get("description") or item.get("body_html") or ""
        
        # Prices
        price = 0.0
        sale_price = 0.0
        
        # Shopify format
        variants = item.get("variants", [])
        if variants and isinstance(variants, list):
            v = variants[0]
            price = clean_price_string(str(v.get("compare_at_price") or v.get("price") or 0))
            sale_price = clean_price_string(str(v.get("price") or 0)) if v.get("compare_at_price") else 0.0
            sku = sku or v.get("sku")
        else:
            price = clean_price_string(str(item.get("price") or item.get("regular_price") or 0))
            sale_price = clean_price_string(str(item.get("sale_price") or 0))
            
        final_price = sale_price if sale_price > 0 else price
        
        # Brand
        brand = item.get("vendor") or item.get("brand") or ""
        
        # Images
        image = ""
        images = item.get("images", [])
        if isinstance(images, list) and len(images) > 0:
            if isinstance(images[0], dict):
                image = images[0].get("src") or images[0].get("url") or ""
            else:
                image = images[0]
        elif isinstance(item.get("image"), dict):
            image = item.get("image").get("src") or item.get("image").get("url") or ""
            
        # Extract barcode from item or variants
        barcode = item.get("barcode") or item.get("ean") or item.get("upc") or item.get("gtin") or ""
        if variants and isinstance(variants, list):
            v = variants[0]
            barcode = barcode or v.get("barcode") or v.get("ean") or v.get("upc") or v.get("gtin") or ""
        if barcode:
            barcode = str(barcode).strip()
            
        prod_url = urljoin(url, item.get("handle") or item.get("url") or "")
        
        # Verify if matches url or name is non-empty
        if name and final_price > 0:
            return {
                "website": urlparse(url).netloc,
                "name": name,
                "brand": brand,
                "model": extract_model(name),
                "sku": sku,
                "barcode": barcode,
                "price": price,
                "sale_price": sale_price,
                "final_price": final_price,
                "image": image,
                "description": desc,
                "url": prod_url
            }
            
    return {}
