"""Security-code normalization shared by Collector data sources."""


def to_ts_code(code: str) -> str:
    """Return a normalized Tushare-style A-share security code."""
    normalized = str(code).strip().upper()
    if "." in normalized:
        return normalized
    if normalized.startswith(("4", "8")):
        return f"{normalized}.BJ"
    if normalized.startswith(("6", "9")):
        return f"{normalized}.SH"
    return f"{normalized}.SZ"
