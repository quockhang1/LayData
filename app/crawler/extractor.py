import json
import re
# pyrefly: ignore [missing-import]
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from app.matching.model_extractor import extract_model

def clean_price_string(price_str: str) -> float:
    """
    Cleans a price string like '12.990.000 đ', '12,990,000', '$15.99' and returns float.
    """
    if not price_str:
        return 0.0
    # Strip non-digits and punctuation separators
    # Standardize: remove non-numeric chars except dot/comma
    cleaned = re.sub(r'[^\d.,]', '', price_str)
    if not cleaned:
        return 0.0
        
    # Check if format uses dot as thousand separator or decimal (e.g. 12.990.000 or 15.99)
    # If there are multiple dots/commas, it's thousand separators
    dots = cleaned.count('.')
    commas = cleaned.count(',')
    
    if dots > 1:
        cleaned = cleaned.replace('.', '')
    elif commas > 1:
        cleaned = cleaned.replace(',', '')
    elif dots == 1 and commas == 1:
        # e.g. 12,990.00 or 12.990,00
        dot_idx = cleaned.find('.')
        comma_idx = cleaned.find(',')
        if dot_idx > comma_idx:
            # dot is decimal, comma is thousand separator
            cleaned = cleaned.replace(',', '')
        else:
            # comma is decimal, dot is thousand separator
            cleaned = cleaned.replace('.', '').replace(',', '.')
    elif dots == 1 and len(cleaned.split('.')[1]) == 3:
        # e.g. 12.990 (likely thousand separator, standard in VND prices)
        cleaned = cleaned.replace('.', '')
    elif commas == 1 and len(cleaned.split(',')[1]) == 3:
        cleaned = cleaned.replace(',', '')
    elif commas == 1:
        # standard decimal comma
        cleaned = cleaned.replace(',', '.')
        
    try:
        return float(cleaned)
    except ValueError:
        return 0.0

def calculate_data_quality_score(product: dict) -> int:
    """
    Calculate quality score 0-100:
    - Has model: +40
    - Has price: +20
    - Has image: +10
    - Has brand: +10
    - Has SKU: +20
    """
    score = 0
    if product.get("model"):
        score += 40
    if product.get("price") or product.get("sale_price") or product.get("final_price"):
        score += 20
    if product.get("image"):
        score += 10
    if product.get("brand"):
        score += 10
    if product.get("sku"):
        score += 20
    return score

def extract_json_ld(soup: BeautifulSoup) -> list:
    """
    Extract products from schema.org / JSON-LD tags.
    """
    products = []
    scripts = soup.find_all("script", type="application/ld+json")
    for script in scripts:
        try:
            data = json.loads(script.string or "")
            # JSON-LD can be a single object or list, or nested @graph
            graph = []
            if isinstance(data, dict):
                if "@graph" in data:
                    graph = data["@graph"]
                else:
                    graph = [data]
            elif isinstance(data, list):
                graph = data
                
            for item in graph:
                if isinstance(item, dict) and item.get("@type") == "Product":
                    products.append(parse_json_ld_product(item))
        except Exception:
            continue
    return products

def parse_json_ld_product(item: dict) -> dict:
    offers = item.get("offers", {})
    price = 0.0
    sale_price = 0.0
    
    if isinstance(offers, dict):
        price = clean_price_string(str(offers.get("price", 0)))
        # Microdata / JSON-LD often holds currency or priceValidUntil. 
        # In custom schemas, they might specify lowPrice / highPrice / price
    elif isinstance(offers, list) and len(offers) > 0:
        price = clean_price_string(str(offers[0].get("price", 0)))

    # Look for sale price equivalents or discount
    image_list = item.get("image", "")
    image = ""
    if isinstance(image_list, list) and len(image_list) > 0:
        image = image_list[0]
    elif isinstance(image_list, str):
        image = image_list
        
    barcode = item.get("gtin13") or item.get("gtin8") or item.get("gtin12") or item.get("gtin14") or item.get("gtin") or item.get("isbn") or ""
    return {
        "name": item.get("name", ""),
        "brand": item.get("brand", {}).get("name", "") if isinstance(item.get("brand"), dict) else str(item.get("brand", "")),
        "model": item.get("model", "") or extract_model(item.get("name", "")),
        "sku": item.get("sku", "") or item.get("mpn", ""),
        "barcode": str(barcode).strip() if barcode else "",
        "price": price,
        "sale_price": sale_price,
        "image": image,
        "description": item.get("description", "")
    }

def extract_meta_tags(soup: BeautifulSoup) -> dict:
    """
    Extract OpenGraph and meta tag properties.
    """
    meta_data = {}
    for meta in soup.find_all("meta"):
        prop = meta.get("property") or meta.get("name")
        val = meta.get("content")
        if prop and val:
            meta_data[prop.lower()] = val
            
    # Try finding typical fields
    name = meta_data.get("og:title") or meta_data.get("twitter:title") or (soup.title.string if soup.title else "")
    image = meta_data.get("og:image") or meta_data.get("twitter:image")
    desc = meta_data.get("og:description") or meta_data.get("description")
    
    # Prices in metas (custom web apps and Shopify/WooCommerce often write these)
    price = meta_data.get("product:price:amount") or meta_data.get("price") or meta_data.get("og:price:amount")
    
    barcode = meta_data.get("product:isbn") or meta_data.get("og:isbn") or meta_data.get("product:upc") or meta_data.get("product:ean") or meta_data.get("product:gtin")
    
    return {
        "name": name.strip() if name else "",
        "image": image or "",
        "description": desc or "",
        "price": clean_price_string(price) if price else 0.0,
        "barcode": str(barcode).strip() if barcode else ""
    }

def extract_custom_html_product(soup: BeautifulSoup, url: str) -> dict:
    """
    Heuristics to extract product details from raw HTML structure without configuring selectors.
    - Title: Finds h1 tag.
    - Price: Finds elements containing 'đ', 'VND', or similar matching pattern.
    - Image: Finds main visual img (often has product, detailed, zoom class, or largest dimensions).
    """
    # Name
    title_el = soup.find("h1")
    name = title_el.text.strip() if title_el else ""
    
    # Prices
    price = 0.0
    sale_price = 0.0
    
    # Look for currency indicators
    price_elements = soup.find_all(text=re.compile(r'\d+[\d.,]*\s*(?:đ|VND|VNĐ|Vnđ|\$)', re.IGNORECASE))
    prices_found = []
    for el in price_elements:
        p_val = clean_price_string(el)
        if p_val > 1000:  # Avoid small false positive numbers
            prices_found.append(p_val)
            
    if len(prices_found) >= 2:
        # E-commerce details typically list Sale Price first or in larger size, and Original Price.
        # Let's assign lowest as sale, highest as regular.
        prices_found = sorted(list(set(prices_found)))
        sale_price = prices_found[0]
        price = prices_found[-1]
    elif len(prices_found) == 1:
        price = prices_found[0]
        
    # Image
    images = soup.find_all("img")
    image_url = ""
    # Filter out icons/logos, pick largest or one with product keywords in src
    for img in images:
        src = img.get("src") or img.get("data-src") or img.get("lazy-src")
        if not src:
            continue
        src_lower = src.lower()
        if "logo" in src_lower or "icon" in src_lower or "banner" in src_lower:
            continue
        if "product" in src_lower or "uploads" in src_lower or "san-pham" in src_lower or "detail" in src_lower:
            image_url = urljoin(url, src)
            break
    if not image_url and images:
        # Default to first decent-sized image
        image_url = urljoin(url, images[0].get("src", ""))
        
    # Brand heuristics
    brand = ""
    brand_el = soup.find(text=re.compile(r'Thương hiệu|Brand|Hãng sản xuất', re.IGNORECASE))
    if brand_el:
        parent = brand_el.parent
        brand = parent.text.replace(brand_el, "").strip(" :-\n")
        
    barcode = ""
    barcode_el = soup.find(text=re.compile(r'\b(?:ean|upc|gtin|barcode|mã vạch)\b', re.IGNORECASE))
    if barcode_el:
        parent = barcode_el.parent
        match = re.search(r'\b\d{8,14}\b', parent.text)
        if match:
            barcode = match.group(0)
            
    return {
        "name": name,
        "price": price,
        "sale_price": sale_price,
        "image": image_url,
        "brand": brand,
        "description": "",
        "barcode": barcode
    }

def auto_extract_product_details(html_content: str, url: str) -> dict:
    """
    Main extraction orchestrator for single detail page.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    
    # 1. Try JSON-LD
    json_ld_products = extract_json_ld(soup)
    if json_ld_products:
        prod = json_ld_products[0]
        # Fallback to metadata image/og:image if JSON-LD image is empty
        meta = extract_meta_tags(soup)
        if not prod.get("image"):
            prod["image"] = meta.get("image") or ""
        # Make sure image url is absolute
        if prod.get("image"):
            prod["image"] = urljoin(url, prod["image"])
        # Deduce final price priority rules
        if prod.get("sale_price", 0) > 0:
            prod["final_price"] = prod["sale_price"]
        else:
            prod["final_price"] = prod.get("price", 0.0)
        prod["website"] = urlparse(url).netloc
        prod["url"] = url
        if not prod.get("barcode"):
            prod["barcode"] = meta.get("barcode") or ""
        return prod
        
    # 2. Try metadata
    meta = extract_meta_tags(soup)
    
    # 3. Try heuristics / Custom HTML
    custom = extract_custom_html_product(soup, url)
    
    # Merge findings (favor json-ld -> meta -> heuristics)
    final_prod = {
        "name": meta.get("name") or custom.get("name") or "",
        "brand": custom.get("brand") or "",
        "model": extract_model(meta.get("name") or custom.get("name") or ""),
        "sku": "",
        "barcode": meta.get("barcode") or custom.get("barcode") or "",
        "price": meta.get("price") or custom.get("price") or 0.0,
        "sale_price": custom.get("sale_price") or 0.0,
        "image": meta.get("image") or custom.get("image") or "",
        "description": meta.get("description") or ""
    }
    
    # Deduce final price priority rules
    # "If sale price exists, final_price = sale_price. Else final_price = regular_price"
    if final_prod["sale_price"] > 0:
        final_prod["final_price"] = final_prod["sale_price"]
    else:
        final_prod["final_price"] = final_prod["price"]
        
    # Domain as website
    parsed = urlparse(url)
    final_prod["website"] = parsed.netloc
    final_prod["url"] = url
    
    return final_prod
