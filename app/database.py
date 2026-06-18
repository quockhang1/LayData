import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "price_intelligence.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. urls table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS urls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE NOT NULL,
        status TEXT DEFAULT 'pending',
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. products_raw table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products_raw (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url_id INTEGER,
        website TEXT NOT NULL,
        name TEXT NOT NULL,
        model TEXT,
        brand TEXT,
        price REAL,
        sale_price REAL,
        final_price REAL NOT NULL,
        sku TEXT,
        barcode TEXT,
        image TEXT,
        url TEXT UNIQUE NOT NULL,
        description TEXT,
        category TEXT,
        stock_status TEXT,
        data_quality_score INTEGER NOT NULL,
        crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(url_id) REFERENCES urls(id)
    );
    """)

    # 3. products_grouped table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products_grouped (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_name TEXT NOT NULL,
        model TEXT,
        brand TEXT,
        sku TEXT,
        barcode TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 4. product_group_mapping table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS product_group_mapping (
        grouped_id INTEGER NOT NULL,
        raw_id INTEGER NOT NULL UNIQUE,
        confidence REAL,
        match_priority TEXT,
        PRIMARY KEY (grouped_id, raw_id),
        FOREIGN KEY(grouped_id) REFERENCES products_grouped(id) ON DELETE CASCADE,
        FOREIGN KEY(raw_id) REFERENCES products_raw(id) ON DELETE CASCADE
    );
    """)

    # 5. price_history table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_product_id INTEGER NOT NULL,
        website TEXT NOT NULL,
        price REAL NOT NULL,
        crawl_date DATE NOT NULL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(raw_product_id) REFERENCES products_raw(id) ON DELETE CASCADE
    );
    """)

    # 6. crawl_logs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS crawl_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        products_found INTEGER DEFAULT 0,
        execution_time REAL,
        error_message TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 7. failed_urls table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS failed_urls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE NOT NULL,
        reason TEXT,
        retry_count INTEGER DEFAULT 0,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 8. price_alerts table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS price_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grouped_id INTEGER NOT NULL,
        target_price REAL NOT NULL,
        email TEXT,
        webhook_url TEXT,
        telegram_chat_id TEXT,
        discord_webhook TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(grouped_id) REFERENCES products_grouped(id) ON DELETE CASCADE
    );
    """)

    # 9. settings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    # Migration helper for barcode column additions if running on existing databases
    try:
        cursor.execute("ALTER TABLE products_raw ADD COLUMN barcode TEXT;")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE products_grouped ADD COLUMN barcode TEXT;")
    except sqlite3.OperationalError:
        pass

    # Create Indexes for Search / Matching Speed
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_raw_model ON products_raw(model);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_raw_sku ON products_raw(sku);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_raw_barcode ON products_raw(barcode);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_grouped_model ON products_grouped(model);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_grouped_sku ON products_grouped(sku);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_grouped_barcode ON products_grouped(barcode);")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
