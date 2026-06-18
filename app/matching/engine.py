import sqlite3
from rapidfuzz import fuzz
from app.database import get_connection
from app.matching.normalizer import normalize_product_name
from app.matching.model_extractor import extract_model
from app.matching.embedder import get_embedding, calculate_similarity
import logging

logger = logging.getLogger(__name__)

# Cache embeddings in memory to speed up multi-product matching during crawl batch runs
_embedding_cache = {}

def get_cached_embedding(text: str):
    if text not in _embedding_cache:
        try:
            _embedding_cache[text] = get_embedding(text)
        except Exception as e:
            logger.error(f"Embedding error for '{text}': {e}")
            return None
    return _embedding_cache[text]

def find_or_create_group(raw_product: dict, conn: sqlite3.Connection) -> tuple:
    """
    Find matching product group using Priority 1-5 pipeline or create a new group.
    Returns: (grouped_id, confidence, match_priority)
    """
    cursor = conn.cursor()
    
    raw_name = raw_product.get("name", "")
    raw_model = raw_product.get("model") or extract_model(raw_name)
    raw_brand = raw_product.get("brand", "")
    raw_sku = raw_product.get("sku", "")
    raw_barcode = raw_product.get("barcode", "")
    
    normalized_name = normalize_product_name(raw_name)
    
    # Priority 1: Model Match
    if raw_model:
        cursor.execute("SELECT id, canonical_name FROM products_grouped WHERE UPPER(model) = ?", (raw_model.upper(),))
        row = cursor.fetchone()
        if row:
            return row[0], 1.0, "model"
            
    # Priority 2: Barcode Match (EAN, UPC, GTIN)
    if raw_barcode:
        cursor.execute("SELECT id, canonical_name FROM products_grouped WHERE UPPER(barcode) = ?", (str(raw_barcode).upper(),))
        row = cursor.fetchone()
        if row:
            return row[0], 1.0, "barcode"
            
    # Priority 3: SKU Match
    if raw_sku:
        cursor.execute("SELECT id, canonical_name FROM products_grouped WHERE UPPER(sku) = ?", (raw_sku.upper(),))
        row = cursor.fetchone()
        if row:
            return row[0], 1.0, "sku"
            
    # Priority 4: Embedding Similarity
    # Fetch all existing groups to calculate embeddings similarity
    cursor.execute("SELECT id, canonical_name, model, brand FROM products_grouped")
    all_groups = cursor.fetchall()
    
    best_grouped_id = None
    best_similarity = 0.0
    
    raw_emb_text = f"{raw_brand} {raw_name} {raw_model or ''}".strip()
    raw_emb = get_cached_embedding(raw_emb_text)
    
    if raw_emb is not None:
        for group in all_groups:
            g_id = group["id"]
            g_name = group["canonical_name"]
            g_brand = group["brand"] or ""
            g_model = group["model"] or ""
            
            g_emb_text = f"{g_brand} {g_name} {g_model}".strip()
            g_emb = get_cached_embedding(g_emb_text)
            
            if g_emb is not None:
                sim = calculate_similarity(raw_emb, g_emb)
                if sim > best_similarity:
                    best_similarity = sim
                    best_grouped_id = g_id
                    
        if best_similarity >= 0.95:
            return best_grouped_id, best_similarity, "embedding"
            
    # Priority 5: RapidFuzz fallback
    best_fuzzy_id = None
    best_fuzzy_ratio = 0.0
    for group in all_groups:
        g_id = group["id"]
        g_name_norm = normalize_product_name(group["canonical_name"])
        
        # Calculate combined ratio
        r1 = fuzz.token_set_ratio(normalized_name, g_name_norm)
        r2 = fuzz.token_sort_ratio(normalized_name, g_name_norm)
        r3 = fuzz.partial_ratio(normalized_name, g_name_norm)
        avg_ratio = (r1 + r2 + r3) / 3.0
        
        if avg_ratio > best_fuzzy_ratio:
            best_fuzzy_ratio = avg_ratio
            best_fuzzy_id = g_id
            
    if best_fuzzy_ratio >= 90.0:
        return best_fuzzy_id, best_fuzzy_ratio / 100.0, "fuzzy"
        
    # None matched, create a new group
    # Generate canonical name: Brand + Model if available, otherwise cleaned title
    import re
    if raw_brand and raw_model:
        canonical_name = f"{raw_brand.strip().title()} {raw_model.upper()}"
    else:
        clean_name = raw_name
        marketing_words = [
            r'\bchính\s+hãng\b', r'\bchinh\s+hang\b', r'\bgiá\s+rẻ\b', r'\bgia\s+re\b',
            r'\bnhập\s+khẩu\b', r'\bnhap\s+khau\b', r'\bđức\b', r'\bduc\b', 
            r'\bgiá\s+tốt\b', r'\bgia\s+tot\b', r'\bkhuyến\s+mãi\b', r'\bkhuyen\s+mai\b',
            r'\bxách\s+tay\b', r'\bxach\s+tay\b', r'\bcao\s+cấp\b', r'\bcao\s+cap\b'
        ]
        for word_pat in marketing_words:
            clean_name = re.sub(word_pat, '', clean_name, flags=re.IGNORECASE)
        clean_name = re.sub(r'\s+', ' ', clean_name).strip()
        canonical_name = clean_name
        
    cursor.execute(
        "INSERT INTO products_grouped (canonical_name, model, brand, sku, barcode) VALUES (?, ?, ?, ?, ?)",
        (canonical_name, raw_model, raw_brand, raw_sku, raw_barcode)
    )
    new_id = cursor.lastrowid
    return new_id, 1.0, "new"

def trigger_notification(alert: dict, raw_product: dict, current_price: float):
    import requests
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT key, value FROM settings")
        settings = {row["key"]: row["value"] for row in cursor.fetchall()}
    except Exception as e:
        logger.error(f"Error reading settings: {e}")
        settings = {}
    finally:
        conn.close()
        
    prod_name = raw_product["name"]
    website = raw_product["website"]
    url = raw_product["url"]
    
    msg = f"🔔 PRICE ALERT: {prod_name} is now available at {current_price:,.0f} VND on {website} (Target: {alert['target_price']:,.0f} VND).\nLink: {url}"
    
    # Telegram Bot
    tg_token = settings.get("telegram_bot_token")
    tg_chat = alert.get("telegram_chat_id") or settings.get("telegram_chat_id")
    if tg_token and tg_chat:
        try:
            tg_url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
            requests.post(tg_url, json={"chat_id": tg_chat, "text": msg}, timeout=10)
            logger.info(f"Telegram notification sent to chat {tg_chat}")
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
            
    # Discord Webhook
    discord_webhook = alert.get("discord_webhook") or settings.get("discord_webhook")
    if discord_webhook:
        try:
            requests.post(discord_webhook, json={"content": msg}, timeout=10)
            logger.info(f"Discord notification sent")
        except Exception as e:
            logger.error(f"Failed to send Discord alert: {e}")
            
    # Webhook URL
    webhook_url = alert.get("webhook_url") or settings.get("webhook_url")
    if webhook_url:
        try:
            payload = {
                "event": "price_alert",
                "product_name": prod_name,
                "price": current_price,
                "target_price": alert['target_price'],
                "website": website,
                "url": url
            }
            requests.post(webhook_url, json=payload, timeout=10)
            logger.info(f"Webhook notification sent to {webhook_url}")
        except Exception as e:
            logger.error(f"Failed to send Webhook alert: {e}")
            
    # Email alert (simulation)
    email_addr = alert.get("email") or settings.get("email")
    if email_addr:
        logger.info(f"[SIMULATED EMAIL ALERT] Sent to {email_addr}. Content: {msg}")

def process_raw_product_matching(raw_product_id: int):
    """
    Process matching workflow for a single raw product.
    Updates the database with mapping.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM products_raw WHERE id = ?", (raw_product_id,))
        raw_product = cursor.fetchone()
        if not raw_product:
            return
            
        grouped_id, confidence, match_priority = find_or_create_group(dict(raw_product), conn)
        
        # Insert mapping
        cursor.execute(
            "INSERT OR REPLACE INTO product_group_mapping (grouped_id, raw_id, confidence, match_priority) VALUES (?, ?, ?, ?)",
            (grouped_id, raw_product_id, confidence, match_priority)
        )
        
        # Record to price history
        cursor.execute(
            "INSERT INTO price_history (raw_product_id, website, price, crawl_date) VALUES (?, ?, ?, date('now'))",
            (raw_product_id, raw_product["website"], raw_product["final_price"])
        )
        
        # Check active price alerts for this product group
        cursor.execute("SELECT * FROM price_alerts WHERE grouped_id = ? AND status = 'active'", (grouped_id,))
        alerts = cursor.fetchall()
        for alert_row in alerts:
            alert = dict(alert_row)
            target = alert["target_price"]
            current_price = raw_product["final_price"]
            if current_price <= target:
                trigger_notification(alert, dict(raw_product), current_price)
                # Mark alert as triggered
                cursor.execute("UPDATE price_alerts SET status = 'triggered' WHERE id = ?", (alert["id"],))
                
        conn.commit()
    except Exception as e:
        logger.error(f"Error in matching for raw product {raw_product_id}: {e}")
        conn.rollback()
    finally:
        conn.close()
