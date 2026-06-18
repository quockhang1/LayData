import requests
import time
from urllib.parse import urlparse
from app.crawler.anti_blocking import get_random_headers
from app.crawler.extractor import auto_extract_product_details
import logging

logger = logging.getLogger(__name__)

# Create session pool map per website domain to persist cookies/sessions
_session_pool = {}

def get_session(domain: str) -> requests.Session:
    if domain not in _session_pool:
        session = requests.Session()
        session.headers.update(get_random_headers())
        _session_pool[domain] = session
    return _session_pool[domain]

def fetch_with_requests(url: str, retries: int = 3, backoff_factor: float = 1.5) -> dict:
    """
    Fetch URL with requests mode.
    Retries on: 403, 429, 500, 502, 503, 504, Connection error, SSL error, Timeout.
    """
    parsed = urlparse(url)
    domain = parsed.netloc
    session = get_session(domain)
    
    status_codes_to_retry = {403, 429, 500, 502, 503, 504}
    
    last_err = ""
    for attempt in range(retries):
        try:
            # Random delay to simulate human timing
            time.sleep(random_delay())
            
            # Rotate headers to reduce blocking
            session.headers.update(get_random_headers())
            
            response = session.get(url, timeout=15, allow_redirects=True)
            
            if response.status_code in status_codes_to_retry:
                last_err = f"HTTP Status {response.status_code}"
                raise requests.RequestException(last_err)
                
            response.raise_for_status()
            
            # Extract product
            product = auto_extract_product_details(response.text, url)
            
            if product.get("name") and product.get("final_price") > 0:
                return {"status": "success", "product": product}
            else:
                return {"status": "partial", "reason": "No products extracted, checking next mode"}
                
        except (requests.RequestException, Exception) as e:
            last_err = str(e)
            sleep_time = backoff_factor ** attempt
            logger.warning(f"Requests mode failed for {url} (Attempt {attempt+1}/{retries}): {e}. Retrying in {sleep_time:.2f}s...")
            time.sleep(sleep_time)
            
    return {"status": "failed", "reason": last_err}

def random_delay() -> float:
    import random
    return random.uniform(0.5, 2.0)
