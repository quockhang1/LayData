import urllib.parse

def analyze_url_type(url: str) -> str:
    """
    Analyze URL structure to detect:
    - product
    - category
    - search
    - collection
    - homepage
    """
    if not url:
        return "homepage"
        
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    query = parsed.query.lower()
    
    # Homepage checks
    if path == "/" or path == "" or path == "/index.html" or path == "/index.php":
        return "homepage"
        
    # Search checks
    if "search" in path or "search" in query or "q=" in query or "s=" in query:
        return "search"
        
    # Collection or category checks
    if any(x in path for x in ["/collection", "/danh-muc", "/category", "/cat-", "/collections/"]):
        return "category"
        
    # Standard e-commerce detail indicators (e.g. .html or specific patterns)
    if path.endswith(".html") or path.endswith(".htm"):
        # Most detailed pages in Vietnam (bep365, nguyenkim) use .html for products
        return "product"
        
    # If path contains multiple segments and doesn't look like generic collection
    segments = [s for s in path.split("/") if s]
    if len(segments) >= 2:
        return "product"
        
    return "category"
