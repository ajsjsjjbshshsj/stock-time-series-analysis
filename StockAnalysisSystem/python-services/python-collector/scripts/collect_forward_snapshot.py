"""One-shot file acquisition; no database, Kafka or credentials in output."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.market_data.forward_snapshot import collect_calendar, collect_market_extension


def main(argv=None, *, client=None, clock=time.time, sleep=time.sleep):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('calendar','market'), required=True)
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args(argv)
    try:
        contract=json.loads(args.contract.read_text(encoding='utf-8'))
        interval=.5
        if client is None:
            import tushare as ts
            from app.config import TUSHARE_TOKEN, COLLECTION_CONFIG
            from app.market_data.tushare_client import TushareClient
            client=TushareClient(ts.pro_api(TUSHARE_TOKEN, timeout=20))
            interval=COLLECTION_CONFIG['request_interval']
        if args.stage=='calendar':
            collect_calendar(client,contract['start'],contract['end'],args.output,interval=interval,clock=clock,sleep=sleep)
        else:
            collect_market_extension(client,contract['codes'],contract['dates'],args.output,binding=contract['binding'],
                                     interval=interval,clock=clock,sleep=sleep)
        print('FORWARD_ACQUISITION_COMPLETE '+args.stage)
        return 0
    except Exception:
        print('FORWARD_ACQUISITION_FAILED: check safe acquisition evidence, coverage and quota deadline.',file=sys.stderr)
        return 1


if __name__=='__main__':
    sys.exit(main())
