import re

# Common patterns matching e-commerce models (e.g., PID675DC1E, SMS6ZCI49E, EH-888, KF-FL68)
MODEL_REGEX = re.compile(
    r'\b(?:[A-Z]+[0-9]+[A-Z0-9\-]*|[0-9]+[A-Z]+[A-Z0-9\-]*|[A-Z]{2,}\-[A-Z0-9]+)\b',
    re.IGNORECASE
)

def extract_model(name: str) -> str:
    """
    Extract model codes from product names and normalize them.
    Examples:
    - PID675DC1E -> PID675DC1E
    - EH-888 -> EH888
    - KF-FL68 -> KFFL68
    """
    if not name:
        return ""
    
    matches = MODEL_REGEX.findall(name)
    if not matches:
        # Fallback to check for simpler structures like Brand name + digits
        fallback_match = re.search(r'\b[A-Z]{2,}\s?\d{3,}\b', name, re.IGNORECASE)
        if fallback_match:
            model = fallback_match.group(0)
            return re.sub(r'[\s\-]', '', model).upper()
        return ""
    
    # Sort by length descending, and return the longest match to capture the full model code
    matches.sort(key=len, reverse=True)
    best_match = matches[0]
    
    # Normalize model (remove dashes, spaces, make uppercase)
    normalized_model = re.sub(r'[\s\-]', '', best_match).upper()
    
    # Ignore values that are clearly just pure numbers or very short strings
    if normalized_model.isdigit() and len(normalized_model) < 4:
        return ""
        
    return normalized_model
