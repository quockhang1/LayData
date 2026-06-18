import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import sqlite3
import pandas as pd

from app.database import init_db, get_connection
from app.crawler.pipeline import crawl_url_pipeline
from app.reporting.excel_generator import generate_excel_report
import logging

# Ensure database is initialized
init_db()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Universal Product Price Intelligence System")

def df_to_dict_safe(df: pd.DataFrame) -> list:
    return df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")

# Thread pool for synchronous crawler pipeline workers
executor = ThreadPoolExecutor(max_workers=50)

# Global configuration variable for concurrency
active_crawls_tasks = {}

@app.post("/crawl")
async def start_single_crawl(url: str, background_tasks: BackgroundTasks):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR IGNORE INTO urls (url, status) VALUES (?, 'pending')", (url,))
        conn.commit()
        cursor.execute("SELECT id FROM urls WHERE url = ?", (url,))
        url_id = cursor.fetchone()[0]
    finally:
        conn.close()
        
    background_tasks.add_task(crawl_url_pipeline, url, url_id)
    return {"status": "started", "url": url}

@app.post("/crawl-batch")
async def start_batch_crawl(
    file: UploadFile = File(...), 
    concurrency: int = Form(20),
    background_tasks: BackgroundTasks = None
):
    content = await file.read()
    urls = [line.decode("utf-8").strip() for line in content.splitlines() if line.strip()]
    
    if not urls:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        
    conn = get_connection()
    cursor = conn.cursor()
    inserted_ids = []
    try:
        for url in urls:
            cursor.execute("INSERT OR IGNORE INTO urls (url, status) VALUES (?, 'pending')", (url,))
            conn.commit()
            cursor.execute("SELECT id FROM urls WHERE url = ?", (url,))
            url_id = cursor.fetchone()[0]
            inserted_ids.append((url, url_id))
    finally:
        conn.close()
        
    # Queue the concurrent execution background worker orchestrator
    background_tasks.add_task(run_batch_scheduler, inserted_ids, concurrency)
    
    return {"status": "batch_queued", "total_urls": len(urls), "concurrency": concurrency}

async def run_batch_scheduler(urls_list: list, concurrency: int):
    """
    Executes tasks using the configured concurrency level.
    """
    loop = asyncio.get_running_loop()
    semaphore = asyncio.Semaphore(concurrency)
    
    async def sem_crawl(url, url_id):
        async with semaphore:
            # Execute the synchronous crawler pipeline in the thread executor
            await loop.run_in_executor(executor, crawl_url_pipeline, url, url_id)
            
    tasks = [sem_crawl(url, url_id) for url, url_id in urls_list]
    await asyncio.gather(*tasks, return_exceptions=True)

@app.get("/products")
def get_products(
    search: str = None, 
    brand: str = None, 
    website: str = None,
    sort_by: str = "newest"
):
    conn = get_connection()
    query = "SELECT * FROM products_raw WHERE 1=1"
    params = []
    
    if search:
        query += " AND (name LIKE ? OR model LIKE ? OR sku LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    if brand:
        query += " AND brand = ?"
        params.append(brand)
    if website:
        query += " AND website = ?"
        params.append(website)
        
    if sort_by == "lowest_price":
        query += " ORDER BY final_price ASC"
    elif sort_by == "highest_price":
        query += " ORDER BY final_price DESC"
    else:
        query += " ORDER BY crawled_at DESC"
        
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df_to_dict_safe(df)

@app.get("/comparison")
def get_comparison(
    search: str = None,
    sort_by: str = "largest_diff"
):
    conn = get_connection()
    query = """
    SELECT 
        pg.id as group_id,
        pg.canonical_name as product_name,
        pg.model,
        pg.brand,
        MIN(pr.final_price) as lowest_price,
        MAX(pr.final_price) as highest_price,
        (MAX(pr.final_price) - MIN(pr.final_price)) as price_difference,
        ROUND(((MAX(pr.final_price) - MIN(pr.final_price)) / MIN(pr.final_price)) * 100, 2) as difference_percent,
        COUNT(DISTINCT pr.website) as store_count
    FROM products_grouped pg
    JOIN product_group_mapping pgm ON pg.id = pgm.grouped_id
    JOIN products_raw pr ON pgm.raw_id = pr.id
    WHERE 1=1
    """
    params = []
    if search:
        query += " AND (pg.canonical_name LIKE ? OR pg.model LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
        
    query += " GROUP BY pg.id"
    
    if sort_by == "largest_diff":
        query += " ORDER BY price_difference DESC"
    elif sort_by == "lowest_price":
        query += " ORDER BY lowest_price ASC"
    elif sort_by == "highest_price":
        query += " ORDER BY highest_price DESC"
    elif sort_by == "most_stores":
        query += " ORDER BY store_count DESC"
    else:
        query += " ORDER BY pg.created_at DESC"
        
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df_to_dict_safe(df)

@app.get("/stats")
def get_stats():
    conn = get_connection()
    cursor = conn.cursor()
    
    total_urls = cursor.execute("SELECT COUNT(*) FROM urls").fetchone()[0]
    success_urls = cursor.execute("SELECT COUNT(*) FROM urls WHERE status = 'completed'").fetchone()[0]
    failed_urls = cursor.execute("SELECT COUNT(*) FROM urls WHERE status = 'failed'").fetchone()[0]
    total_products = cursor.execute("SELECT COUNT(*) FROM products_raw").fetchone()[0]
    total_groups = cursor.execute("SELECT COUNT(*) FROM products_grouped").fetchone()[0]
    
    lowest_price = cursor.execute("SELECT MIN(final_price) FROM products_raw").fetchone()[0] or 0.0
    highest_price = cursor.execute("SELECT MAX(final_price) FROM products_raw").fetchone()[0] or 0.0
    avg_price = cursor.execute("SELECT AVG(final_price) FROM products_raw").fetchone()[0] or 0.0
    
    conn.close()
    
    return {
        "total_urls": total_urls,
        "success_urls": success_urls,
        "failed_urls": failed_urls,
        "total_products": total_products,
        "total_groups": total_groups,
        "lowest_price": lowest_price,
        "highest_price": highest_price,
        "avg_price": round(avg_price, 2)
    }

@app.get("/logs")
def get_logs(limit: int = 50):
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM crawl_logs ORDER BY timestamp DESC LIMIT ?", conn, params=[limit])
    conn.close()
    return df_to_dict_safe(df)

@app.get("/failed")
def get_failed():
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM failed_urls ORDER BY timestamp DESC", conn)
    conn.close()
    return df_to_dict_safe(df)

@app.delete("/failed")
def clear_failed_urls():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM failed_urls")
        cursor.execute("DELETE FROM crawl_logs")
        cursor.execute("UPDATE urls SET status = 'pending' WHERE status = 'failed'")
        conn.commit()
        return {"status": "cleared"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/export/excel")
def export_excel():
    excel_path = generate_excel_report()
    return FileResponse(excel_path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename="products_compare.xlsx")

@app.get("/export/raw/excel")
def export_raw_excel():
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM products_raw", conn)
    conn.close()
    path = "products_raw.xlsx"
    df.to_excel(path, index=False)
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename="products_raw.xlsx")

@app.get("/export/csv")
def export_csv():
    conn = get_connection()
    raw_df = pd.read_sql_query("SELECT * FROM products_raw", conn)
    conn.close()
    csv_path = "products_raw.csv"
    raw_df.to_csv(csv_path, index=False)
    return FileResponse(csv_path, media_type="text/csv", filename="products_raw.csv")

@app.get("/export/compare/csv")
def export_compare_csv():
    conn = get_connection()
    query = """
    SELECT 
        pg.canonical_name as product_name,
        pg.model,
        pg.brand,
        MIN(pr.final_price) as lowest_price,
        MAX(pr.final_price) as highest_price,
        (MAX(pr.final_price) - MIN(pr.final_price)) as price_difference,
        ROUND(((MAX(pr.final_price) - MIN(pr.final_price)) / MIN(pr.final_price)) * 100, 2) as difference_percent,
        COUNT(DISTINCT pr.website) as store_count
    FROM products_grouped pg
    JOIN product_group_mapping pgm ON pg.id = pgm.grouped_id
    JOIN products_raw pr ON pgm.raw_id = pr.id
    GROUP BY pg.id
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    path = "products_compare.csv"
    df.to_csv(path, index=False)
    return FileResponse(path, media_type="text/csv", filename="products_compare.csv")

@app.get("/export/compare/json")
def export_compare_json():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, canonical_name, model, brand FROM products_grouped")
    groups = cursor.fetchall()
    results = []
    for g in groups:
        cursor.execute("""
            SELECT pr.website, pr.name, pr.final_price, pr.url, pr.image
            FROM products_raw pr
            JOIN product_group_mapping pgm ON pr.id = pgm.raw_id
            WHERE pgm.grouped_id = ?
        """, (g["id"],))
        stores = [dict(s) for s in cursor.fetchall()]
        if not stores:
            continue
        prices = [s["final_price"] for s in stores]
        low = min(prices)
        high = max(prices)
        diff = high - low
        diff_pct = round((diff / low) * 100, 2) if low > 0 else 0
        results.append({
            "product_name": g["canonical_name"],
            "model": g["model"],
            "brand": g["brand"],
            "lowest_price": low,
            "highest_price": high,
            "difference": diff,
            "difference_percent": diff_pct,
            "stores": stores
        })
    conn.close()
    
    import json
    path = "products_compare.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    return FileResponse(path, media_type="application/json", filename="products_compare.json")

# Price Alert Schemas and Endpoints
class PriceAlertRequest(BaseModel):
    grouped_id: int
    target_price: float
    email: str = None
    telegram_chat_id: str = None
    discord_webhook: str = None
    webhook_url: str = None

@app.post("/alerts")
def create_price_alert(req: PriceAlertRequest):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO price_alerts (grouped_id, target_price, email, telegram_chat_id, discord_webhook, webhook_url, status)
            VALUES (?, ?, ?, ?, ?, ?, 'active')
            """,
            (req.grouped_id, req.target_price, req.email, req.telegram_chat_id, req.discord_webhook, req.webhook_url)
        )
        conn.commit()
        return {"status": "alert_created"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/alerts")
def get_price_alerts():
    conn = get_connection()
    query = """
    SELECT pa.*, pg.canonical_name as product_name
    FROM price_alerts pa
    JOIN products_grouped pg ON pa.grouped_id = pg.id
    ORDER BY pa.created_at DESC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df_to_dict_safe(df)

@app.delete("/alerts/{alert_id}")
def delete_price_alert(alert_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM price_alerts WHERE id = ?", (alert_id,))
        conn.commit()
        return {"status": "alert_deleted"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# Settings persistence
@app.get("/settings")
def get_settings():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings")
    rows = cursor.fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}

class SettingsRequest(BaseModel):
    settings: dict

@app.post("/settings")
def save_settings(req: SettingsRequest):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        for key, val in req.settings.items():
            cursor.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, str(val) if val is not None else "")
            )
        conn.commit()
        return {"status": "settings_saved"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# Historical Price Data for specific group
@app.get("/price-history/{grouped_id}")
def get_price_history(grouped_id: int):
    conn = get_connection()
    query = """
    SELECT ph.crawl_date, ph.website, ph.price
    FROM price_history ph
    JOIN product_group_mapping pgm ON ph.raw_product_id = pgm.raw_id
    WHERE pgm.grouped_id = ?
    ORDER BY ph.crawl_date ASC
    """
    df = pd.read_sql_query(query, conn, params=[grouped_id])
    conn.close()
    return df_to_dict_safe(df)

# Front end route
@app.get("/", response_class=HTMLResponse)
def read_root():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()
