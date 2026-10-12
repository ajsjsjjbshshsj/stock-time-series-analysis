import importlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from preparation_market_support import fixture
from app.preparation.inventory import build_inventory

def test_shared_core_matches_old_fixed_pool_attribution():
    core=importlib.import_module('app.preparation.stock_inventory').build_stock_inventory
    pool,window,tables,evidence=fixture()
    old=build_inventory(pool,window,tables,evidence);new=core(pool,window,tables,evidence)
    assert old==new and new['data_ready'] is True and new['coverage']=={'DATABASE_ROW':6}

def test_new_market_core_does_not_require_registration_as_fake_industry():
    core=importlib.import_module('app.preparation.stock_inventory').build_stock_inventory
    pool,window,tables,evidence=fixture()
    universe=dict(pool_version='market_hand_fixture',codes=pool['codes'],stock_count=2)
    result=core(universe,window,tables,evidence)
    assert result['data_ready'] is True and result['stock_count']==2
