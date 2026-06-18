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
