import time
import sqlite3
from urllib.parse import urlparse
from app.crawler.analyzer import analyze_url_type
from app.crawler.request_mode import fetch_with_requests
from app.crawler.playwright_mode import fetch_with_playwright
from app.crawler.api_discovery import discover_and_fetch_api
from app.crawler.extractor import calculate_data_quality_score
from app.database import get_connection
from app.matching.engine import process_raw_product_matching
import logging

logger = logging.getLogger(__name__)

def extract_product_links(html_content: str, base_url: str) -> list:
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin, urlparse
    
    soup = BeautifulSoup(html_content, "html.parser")
    parsed_base = urlparse(base_url)
    base_domain = parsed_base.netloc
    
    product_links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        full_url = urljoin(base_url, href)
        parsed_url = urlparse(full_url)
        if parsed_url.netloc == base_domain and parsed_url.scheme in ("http", "https"):
            if analyze_url_type(full_url) == "product":
                clean_url = full_url.split("#")[0]
                product_links.add(clean_url)
                
    return list(product_links)

def crawl_url_pipeline(url: str, url_id: int = None) -> dict:
    """
    Crawling Pipeline orchestrator for each URL:
    STEP 1: Analyze URL type.
    STEP 2: Requests Mode. If success, STOP.
    STEP 3: Playwright Mode. If success, STOP.
    STEP 4: API Discovery Mode. If success, STOP.
    If all fails, mark failed.
    """
    start_time = time.time()
    website = urlparse(url).netloc
    
    # STEP 1: Analyze URL type
    url_type = analyze_url_type(url)
    
    if url_type != "product":
        logger.info(f"URL is listing/category page: {url}. Extracting product links.")
        html_content = ""
        mode_used = "requests"
        
        # Try requests
        try:
            from app.crawler.request_mode import get_session, random_delay
            from app.crawler.anti_blocking import get_random_headers
            time.sleep(random_delay())
            session = get_session(website)
            session.headers.update(get_random_headers())
            
            # Setup proxy dynamically
            from app.crawler.anti_blocking import get_proxy_config
            proxy_cfg = get_proxy_config()
            if proxy_cfg:
                host = proxy_cfg["host"]
                port = proxy_cfg["port"]
                user = proxy_cfg["user"]
                pwd = proxy_cfg["pass"]
                if user and pwd:
                    proxy_str = f"http://{user}:{pwd}@{host}:{port}"
                else:
                    proxy_str = f"http://{host}:{port}"
                session.proxies = {
                    "http": proxy_str,
                    "https": proxy_str
                }
            else:
                session.proxies = {}
                
            res = session.get(url, timeout=15, allow_redirects=True)
            if res.status_code == 200:
                html_content = res.text
        except Exception as e:
            logger.warning(f"Requests failed to fetch category {url}: {e}")
            
        # Try playwright
        if not html_content:
            logger.info(f"Falling back to Playwright for category {url}")
            try:
                from playwright.sync_api import sync_playwright
                from app.crawler.anti_blocking import get_random_viewport, get_random_headers
                from app.crawler.playwright_mode import _playwright_semaphore, scroll_to_end
                with _playwright_semaphore:
                    with sync_playwright() as p:
                        viewport = get_random_viewport()
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
                        headers = get_random_headers()
                        context = browser.new_context(
                            user_agent=headers["User-Agent"],
                            viewport=viewport,
                            extra_http_headers={"Accept-Language": headers["Accept-Language"]}
                        )
                        page = context.new_page()
                        page.goto(url, timeout=30000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass
                        scroll_to_end(page)
                        html_content = page.content()
                        browser.close()
                        mode_used = "playwright"
            except Exception as e:
                logger.error(f"Playwright failed to fetch category {url}: {e}")
                
        if html_content:
            product_links = extract_product_links(html_content, url)
            logger.info(f"Discovered {len(product_links)} product links from {url}")
            
            # Save discovered links to the database as pending
            conn = get_connection()
            cursor = conn.cursor()
            try:
                inserted_count = 0
                for link in product_links:
                    cursor.execute("INSERT OR IGNORE INTO urls (url, status) VALUES (?, 'pending')", (link,))
                    if cursor.rowcount > 0:
                        inserted_count += 1
                conn.commit()
                logger.info(f"Inserted {inserted_count} new product URLs into database.")
                
                # Update status of this category URL to completed
                if url_id:
                    cursor.execute("UPDATE urls SET status = 'completed' WHERE id = ?", (url_id,))
                
                # Log success
                cursor.execute("""
                INSERT INTO crawl_logs (url, mode, status, products_found, execution_time)
                VALUES (?, ?, ?, ?, ?)
                """, (url, mode_used, "success", len(product_links), time.time() - start_time))
                conn.commit()
                return {"status": "success", "discovered_links": len(product_links)}
            except Exception as e:
                logger.error(f"Error saving discovered links for {url}: {e}")
                conn.rollback()
            finally:
                conn.close()
                
        # Handle Failure
        conn = get_connection()
        cursor = conn.cursor()
        try:
            if url_id:
                cursor.execute("UPDATE urls SET status = 'failed' WHERE id = ?", (url_id,))
            cursor.execute("""
            INSERT INTO crawl_logs (url, mode, status, products_found, execution_time, error_message)
            VALUES (?, ?, ?, 0, ?, ?)
            """, (url, mode_used, "failed", time.time() - start_time, "Failed to retrieve category page HTML"))
            conn.commit()
        except Exception as e:
            logger.error(f"Error logging failed category {url}: {e}")
            conn.rollback()
        finally:
            conn.close()
            
        return {"status": "failed", "reason": "Failed to fetch category page HTML"}
    
    # STEP 2: Requests Mode
    logger.info(f"Starting Requests Mode for {url}")
    result = fetch_with_requests(url)
    mode_used = "requests"
    
    # STEP 3: Playwright Mode
    if result["status"] != "success":
        logger.info(f"Requests Mode failed for {url}. Starting Playwright Mode.")
        result = fetch_with_playwright(url)
        mode_used = "playwright"
        
    # STEP 4: API Discovery Mode
    if result["status"] != "success":
        logger.info(f"Playwright Mode failed for {url}. Starting API Discovery Mode.")
        result = discover_and_fetch_api(url)
        mode_used = "api"
        
    execution_time = time.time() - start_time
    
    # Handle Success
    if result["status"] == "success":
        product = result["product"]
        # Save raw products
        conn = get_connection()
        cursor = conn.cursor()
        try:
            # Score quality
            q_score = calculate_data_quality_score(product)
            product["data_quality_score"] = q_score
            
            cursor.execute("""
            INSERT OR REPLACE INTO products_raw 
            (url_id, website, name, model, brand, price, sale_price, final_price, sku, barcode, image, url, description, category, stock_status, data_quality_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                url_id,
                product.get("website", website),
                product["name"],
                product.get("model"),
                product.get("brand"),
                product.get("price", 0.0),
                product.get("sale_price", 0.0),
                product["final_price"],
                product.get("sku"),
                product.get("barcode"),
                product.get("image"),
                product.get("url", url),
                product.get("description"),
                product.get("category"),
                product.get("stock_status", "in_stock"),
                q_score
            ))
            raw_id = cursor.lastrowid
            
            # Log success
            cursor.execute("""
            INSERT INTO crawl_logs (url, mode, status, products_found, execution_time)
            VALUES (?, ?, ?, ?, ?)
            """, (url, mode_used, "success", 1, execution_time))
            
            # Update status of URL to completed
            if url_id:
                cursor.execute("UPDATE urls SET status = 'completed' WHERE id = ?", (url_id,))
                
            conn.commit()
            
            # Trigger product matching pipeline synchronously/background
            process_raw_product_matching(raw_id)
            
            return {"status": "success", "product": product}
        except Exception as e:
            logger.error(f"Error saving crawled product for {url}: {e}")
            conn.rollback()
        finally:
            conn.close()
            
    # Handle Failure
    conn = get_connection()
    cursor = conn.cursor()
    try:
        reason = result.get("reason", "Unknown failure")
        # Update URL status
        if url_id:
            cursor.execute("UPDATE urls SET status = 'failed' WHERE id = ?", (url_id,))
            
        # Log failure
        cursor.execute("""
        INSERT INTO crawl_logs (url, mode, status, products_found, execution_time, error_message)
        VALUES (?, ?, ?, 0, ?, ?)
        """, (url, mode_used, "failed", execution_time, reason))
        
        # Save to failed URLs table
        cursor.execute("""
        INSERT OR REPLACE INTO failed_urls (url, reason, retry_count)
        VALUES (?, ?, COALESCE((SELECT retry_count FROM failed_urls WHERE url = ?) + 1, 1))
        """, (url, reason, url))
        
        conn.commit()
    except Exception as e:
        logger.error(f"Error logging failed URL {url}: {e}")
        conn.rollback()
    finally:
        conn.close()
        
    return {"status": "failed", "reason": result.get("reason")}
