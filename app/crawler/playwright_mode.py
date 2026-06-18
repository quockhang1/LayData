from playwright.sync_api import sync_playwright
import time
from app.crawler.anti_blocking import get_random_headers, get_random_viewport
from app.crawler.extractor import auto_extract_product_details
import logging

logger = logging.getLogger(__name__)

from threading import Semaphore

# Restrict to at most 5 concurrent Playwright browser launches to prevent OOM
_playwright_semaphore = Semaphore(5)

def fetch_with_playwright(url: str) -> dict:
    """
    Launch headless browser. Navigate URL.
    Perform: Wait for load state networkidle, Auto Scroll, Load More buttons.
    """
    with _playwright_semaphore:
        try:
            with sync_playwright() as p:
                viewport = get_random_viewport()
                
                # Setup proxy from configurations
                from app.crawler.anti_blocking import get_proxy_config
                proxy_cfg = get_proxy_config()
                proxy_args = {}
                if proxy_cfg:
                    host = proxy_cfg["host"]
                    port = proxy_cfg["port"]
                    user = proxy_cfg["user"]
                    pwd = proxy_cfg["pass"]
                    proxy_args["proxy"] = {
                        "server": f"http://{host}:{port}"
                    }
                    if user:
                        proxy_args["proxy"]["username"] = user
                    if pwd:
                        proxy_args["proxy"]["password"] = pwd
                
                browser = p.chromium.launch(headless=True, **proxy_args)
                
                # Setup context with randomized viewport & user agent
                headers = get_random_headers()
                context = browser.new_context(
                    user_agent=headers["User-Agent"],
                    viewport=viewport,
                    extra_http_headers={"Accept-Language": headers["Accept-Language"]}
                )
                
                page = context.new_page()
                
                # Navigate
                page.goto(url, timeout=30000)
                
                # Wait for network idle
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                    
                # Perform Auto Scroll to load lazy/infinite contents
                scroll_to_end(page)
                
                # Auto-detect "Xem thêm" / "Load more" buttons and click them
                click_load_more_buttons(page)
                
                # Get HTML content
                html_content = page.content()
                
                browser.close()
                
                # Extract product details
                product = auto_extract_product_details(html_content, url)
                if product.get("name") and product.get("final_price") > 0:
                    return {"status": "success", "product": product}
                else:
                    return {"status": "failed", "reason": "No products extracted via Playwright"}
                    
        except Exception as e:
            logger.error(f"Playwright mode error for {url}: {e}")
            return {"status": "failed", "reason": f"Playwright error: {str(e)}"}

def scroll_to_end(page):
    """
    Automatically scrolls page to trigger infinite scroll.
    """
    try:
        for _ in range(5):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            time.sleep(0.5)
    except Exception:
        pass

def click_load_more_buttons(page):
    """
    Look for elements containing Vietnamese/English load more strings:
    'Xem thêm', 'Tải thêm', 'Load more'
    """
    try:
        load_more_selectors = [
            "text=Xem thêm", 
            "text=Tải thêm", 
            "text=Load more", 
            "button:has-text('Xem thêm')", 
            "button:has-text('Tải thêm')"
        ]
        for selector in load_more_selectors:
            # Check visibility and click
            btn = page.locator(selector)
            if btn.count() > 0:
                for idx in range(btn.count()):
                    el = btn.nth(idx)
                    if el.is_visible():
                        el.click(timeout=1000)
                        time.sleep(1)
    except Exception:
        pass
