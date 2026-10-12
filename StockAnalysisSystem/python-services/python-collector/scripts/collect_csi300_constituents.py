"""CSI300 source evidence CLI; credentials remain inside Collector."""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.market_data.csi300_constituent_snapshot import collect_constituent_evidence, verify_constituent_evidence


def main(argv=None, *, client=None, clock=time.time, sleep=time.sleep):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--request-state', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if (args.output/'complete.json').exists():
            verify_constituent_evidence(args.output, args.as_of)
        else:
            interval = .5
            if client is None:
                project = Path(__file__).resolve().parents[3]
                if args.request_state.resolve() != project/'.runtime/market_requests/request_state.json':
                    raise ValueError('Canonical shared request state required')
                import tushare as ts
                from app.config import TUSHARE_TOKEN, COLLECTION_CONFIG
                from app.market_data.tushare_client import TushareClient
                if not TUSHARE_TOKEN: raise ValueError('Credential unavailable')
                client = TushareClient(ts.pro_api(TUSHARE_TOKEN, timeout=20))
                interval = COLLECTION_CONFIG['request_interval']
            collect_constituent_evidence(client, args.as_of, args.output, request_state=args.request_state,
                                         interval=interval, clock=clock, sleep=sleep)
        print('CSI300_ACQUISITION_COMPLETE')
        return 0
    except Exception:
        print('CSI300_ACQUISITION_FAILED: inspect safe evidence and shared quota deadline', file=sys.stderr)
        return 1


if __name__ == '__main__': sys.exit(main())
