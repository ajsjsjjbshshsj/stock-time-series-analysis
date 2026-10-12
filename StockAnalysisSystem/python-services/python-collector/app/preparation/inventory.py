"""Validated fixed-pool wrapper around the shared stock-day inventory core."""
from .contracts import validate_pool
from .stock_inventory import build_stock_inventory

def build_inventory(pool,window,tables,evidence):
    validate_pool(pool)
    return build_stock_inventory(pool,window,tables,evidence)
