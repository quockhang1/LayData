import re
import unicodedata

def remove_vietnamese_accents(text: str) -> str:
    """
    Remove Vietnamese accents from a string.
    """
    if not text:
        return ""
    
    # Standard decomposition
    normalized = unicodedata.normalize('NFKD', text)
    # Remove standard combining characters
    no_accents = "".join([c for c in normalized if not unicodedata.combining(c)])
    
    # Specific manual mappings for characters NFKD might not fully decompose (e.g. 'đ')
    replacements = {
        'đ': 'd', 'Đ': 'D',
        'ô': 'o', 'Ô': 'O',
        'â': 'a', 'Â': 'A',
        'ê': 'e', 'Ê': 'E'
    }
    for accent, clean in replacements.items():
        no_accents = no_accents.replace(accent, clean)
        
    return no_accents

def normalize_product_name(name: str) -> str:
    """
    Normalize product names:
    - Convert to lowercase
    - Remove Vietnamese accents
    - Remove special characters
    - Remove duplicate spaces
    - Remove marketing words (e.g., 'chinh hang', 'gia re', 'nhap khau')
    """
    if not name:
        return ""
    
    # Lowercase
    name_clean = name.lower()
    
    # Remove accents
    name_clean = remove_vietnamese_accents(name_clean)
    
    # Replace common special characters/punctuations with a space
    name_clean = re.sub(r'[\/\\#\$\%\^\&\*\(\)\_\+\=\-\[\]\{\}\;\:\'\"\,\<\>\?\!\~\`\|]', ' ', name_clean)
    
    # Remove marketing/promotional terms
    marketing_words = [
        r'\bchinh\s+hang\b', r'\bgia\s+re\b', r'\bnhap\s+khau\b', r'\bduc\b', 
        r'\bgia\s+tot\b', r'\bkhuyen\s+mai\b', r'\bxach\s+tay\b', r'\bcao\s+cap\b',
        r'\bchinh\s+hang\b', r'\bchinh\s+hang\s+100%\b', r'\bgia\s+shock\b'
    ]
    for word_pat in marketing_words:
        name_clean = re.sub(word_pat, '', name_clean)
        
    # Remove duplicate spaces
    name_clean = re.sub(r'\s+', ' ', name_clean).strip()
    
    return name_clean
