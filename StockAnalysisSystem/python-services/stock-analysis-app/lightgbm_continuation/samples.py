"""Build dated samples from verified stock shards without choosing new features."""
from hashlib import sha256

import numpy as np
import pandas as pd

from tasks.contracts import encoded
from tasks.market_samples import qualify_stock
from workflows.source import history_fingerprint
from .windows import select_windows

MAX_SAMPLE_VALUES = 160_000_000  # 640 MB of float32 inputs before assembling the training matrix.


def build_samples(stocks, *, codes, names, parent, replay_days, cutoff, emit=None):
    frames, prefixes = [], {}
    retained = 0
    for position, (code, snapshot, calendar, proof) in enumerate(stocks, 1):
        if position > len(codes) or codes[position-1] != code:
            raise ValueError("Continuation stream differs from frozen pool")
        raw = snapshot.source_history
        if history_fingerprint(raw, parent["cutoff"]) != parent["prefixes"][code]:
            raise ValueError("Parent historical raw data changed")
        prefixes[code] = history_fingerprint(raw, str(cutoff))
        frame, _ = qualify_stock(snapshot, calendar, proof,
                                 dict(start_date=str(parent["input_start"]), end_date=str(cutoff)))
        if emit and (position % 100 == 0 or position == len(codes)):
            emit("progress", phase="INPUT", completed=position, total=len(codes))
        if frame.empty:
            continue  # Still counted in the frozen coverage denominator.
        frame = frame.rename(columns={"trade_date": "signal_date", "label_target_date": "label_date"})
        for name in ("signal_date", "label_date"):
            frame[name] = frame[name].dt.date
        selected = (frame.signal_date > parent["seen_through"]) | (
            frame.signal_date.isin(replay_days) & (frame.label_date <= parent["train_label_end"]))
        frame = frame.loc[selected, ["ts_code", "signal_date", "label_date", "label", *names]].copy()
        retained += len(frame) * len(names)
        if retained > MAX_SAMPLE_VALUES:
            raise ValueError("Continuation input exceeds bounded sample budget")
        frame[names] = frame[names].astype("float32")
        if not np.isfinite(frame[names].to_numpy()).all():
            raise ValueError("Nonfinite continuation features")
        if len(frame):
            frames.append(frame)
    if list(prefixes) != codes or not frames:
        raise ValueError("Continuation sample pool unavailable")
    rows = pd.concat(frames, ignore_index=True)
    del frames
    windows = select_windows(rows, seen_through=parent["seen_through"], cutoff=cutoff,
                             pool_count=len(codes), replay_days=replay_days)
    wanted = set((*windows.replay, *windows.new, *windows.comparison))
    rows = rows.loc[rows.signal_date.isin(wanted)].sort_values(["signal_date", "ts_code"], ignore_index=True)
    proof = sha256(encoded(names).encode("utf-8"))
    for code, day, target in rows[["ts_code", "signal_date", "label_date"]].itertuples(index=False, name=None):
        proof.update(f"{code}:{day}:{target}\n".encode("ascii"))
    proof.update(rows[names].to_numpy(dtype="<f4").tobytes(order="C"))
    proof.update(rows["label"].to_numpy(dtype="<f8").tobytes())
    evidence = dict(windows.evidence(), sample_sha256=proof.hexdigest())
    return rows, evidence, dict(cutoff=str(cutoff), prefixes=prefixes)
