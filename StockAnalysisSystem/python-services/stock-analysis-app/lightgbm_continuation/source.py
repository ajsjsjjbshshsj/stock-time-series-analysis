"""Verify the parent, bind a concrete sample set, and reserve its holdout."""
from datetime import date
import json
from pathlib import Path

from sqlalchemy import select, text

from prediction_portal.repository import account, release_row
from tasks.artifacts import read_json
from tasks.contracts import Conflict, digest
from tasks.market_samples import open_days
from tasks.repository import events, tasks
from tasks.source import SourceChanged, read_calendar
from workflows.source import WorkflowSource, completed_session
from .contracts import KIND, normalize
from .samples import build_samples


def approved(connection, model_id):
    row = release_row(connection, model_id)
    if not row["enabled"]:
        raise Conflict("Continuation parent is not approved")
    account(connection, row["sponsor_id"], admin=True)
    return row


def assert_current(connection, request, binding):
    current = approved(connection, request["parent_model_id"])
    if (current["revision"], current["proof_sha256"]) != (binding["parent_revision"], binding["parent_proof_sha256"]):
        raise SourceChanged("Continuation parent approval changed")
    row = connection.execute(text(
        "SELECT build_id, source_sha256, state FROM stock_feature_build "
        "WHERE active_slot='traditional_v2'")).mappings().one_or_none()
    if (row is None or row["state"] != "READY" or row["build_id"] != binding["build_id"]
            or row["source_sha256"] != binding["source_sha256"]):
        raise SourceChanged("Continuation feature source changed")
    from automation.repository import publication_pending
    if publication_pending(connection):
        raise SourceChanged("Feature activation recovery required")


RESERVED = "CONTINUATION_RESERVED"


def reserved_window(connection, task_id):
    receipts = connection.execute(select(events.c.detail).where(
        events.c.task_id == task_id, events.c.event == RESERVED).limit(2)).scalars().all()
    if len(receipts) != 1:
        raise ValueError("Exactly one continuation comparison receipt required")
    return json.loads(receipts[0])


def reserve_comparison(connection, request, windows):
    # Task events are durable receipts; cancellation/failure does not free an
    # already disclosed holdout. Caller holds the common execution-slot lock.
    id = request["parent_model_id"]
    first_signal = windows["comparison"][0]
    candidates = connection.execute(select(tasks.c.request, events.c.detail).join(
        events, events.c.task_id == tasks.c.id).where(events.c.event == RESERVED,
        tasks.c.request.contains(f'"kind":"{KIND}"'),
        tasks.c.request.contains(f'"parent_model_id":"{id}"')).limit(1001)).mappings()
    for i, prior in enumerate(candidates):
        if i >= 1000:
            raise Conflict("Continuation reservation limit reached")
        previous = json.loads(prior["request"])
        if previous.get("kind") != KIND or previous.get("parent_model_id") != id:
            continue
        held = json.loads(prior["detail"])
        if first_signal <= held["seen_through"]:
            raise Conflict("Continuation needs a later, undisclosed comparison window")


def engine_identity():
    files = sorted(Path(__file__).parent.glob("*.py"))
    return digest({path.name: path.read_text(encoding="utf-8") for path in files})


def native_header(path):
    """Validate the supported file type without loading LightGBM in the API."""
    with path.open("rb") as stream:
        header = stream.read(256 * 1024).split(b"\nTree=0\n", 1)
    if len(header) != 2:
        raise ValueError("Native LightGBM tree header required")
    lines = set(header[0].splitlines())
    if not {b"tree", b"num_class=1", b"num_tree_per_iteration=1", b"objective=regression"} <= lines:
        raise ValueError("Native single-output LightGBM regression required")


def verify_lifecycle_prefix(old, new, codes, until):
    """Later listing/delisting information must not alter replay eligibility."""
    fields = {"list_date", "delist_date", "valid_from", "valid_until"}

    def clipped(row):
        if row.keys() != fields:
            raise ValueError("Parent lifecycle proof unavailable")
        # End bounds after the last replay label have no historical effect.
        return {k: (v if v is not None and v <= until else
                    "AFTER_REPLAY" if k == "list_date" or k == "valid_from" and v is not None else None)
                for k, v in row.items()}

    if set(old) != set(codes) or set(new) != set(codes):
        raise ValueError("Complete parent and current lifecycles required")
    if any(clipped(old[code]) != clipped(new[code]) for code in codes):
        raise SourceChanged("Parent training lifecycle changed")


class ContinuationSource:
    def __init__(self, engine, calendar_path=None, *, workflow=None):
        self.engine = engine
        self.workflow = workflow or WorkflowSource(engine, calendar_path)
        self.calendar_path = self.workflow.calendar_path

    def parent(self, id):
        with self.engine.connect() as connection:
            release = approved(connection, id)
        row, metadata = self.workflow.model(id)
        frozen = metadata["binding"]["data"]
        native = metadata["native"]
        names = native["feature_names"]
        codes = frozen["stock_codes"]
        proof = release["proof"]
        if (row["algorithm"] != "lightgbm" or native["algorithm"] != "lightgbm"
                or native["score_units"] not in {"raw_return_5d", "within_day_centered_rank"}
                or native["horizon"] != 5 or native["holding_sessions"] != 5
                or proof["algorithm"] != "lightgbm" or proof["manifest_sha256"] != row["manifest_sha256"]
                or proof["stock_codes"] != codes or proof["pool_identity"] != digest(codes)
                or proof["score_units"] != native["score_units"] or proof["model_id"] != id
                or not codes or len(codes) > 10000 or codes != sorted(set(codes))):
            raise ValueError("Approved frozen LightGBM regression required")
        from data_processor.feature_store_contract import FEATURE_COLUMNS
        if not names or len(set(names)) != len(names) or not set(names) <= set(FEATURE_COLUMNS):
            raise ValueError("Continuation feature order unavailable")
        folder = self.workflow.store.path(id)
        native_header(folder / "model.txt")
        history = read_json(folder / "history.json", max_bytes=4*1024*1024)
        if set(history["prefixes"]) != set(codes):
            raise ValueError("Complete parent historical proof required")
        start, end, seen, historical = (date.fromisoformat(x) for x in
            (native["train_start"], native["train_end"], native["seen_through"], history["cutoff"]))
        if not start <= end < seen <= historical:
            raise ValueError("Parent training boundary unavailable")
        return row, metadata, release, history

    def prepare(self, value):
        request = normalize(value)
        id = request["parent_model_id"]
        row, metadata, release, history = self.parent(id)
        native = metadata["native"]
        dates = read_calendar(self.calendar_path)
        opened = open_days(dates)
        source = self.workflow.base.describe()
        cutoff = date.fromisoformat(min(source["source_cutoff"], completed_session(dates)))
        seen = date.fromisoformat(native["seen_through"])
        train_start, train_end = map(date.fromisoformat, (native["train_start"], native["train_end"]))
        if source["build_id"] != request["feature_build_id"] or cutoff < date.fromisoformat(history["cutoff"]):
            raise ValueError("Pinned continuation source unavailable")
        positions = {day: i for i, day in enumerate(opened)}
        if train_end not in positions or positions[train_end]+5 >= len(opened):
            raise ValueError("Parent training calendar unavailable")
        train_label_end = opened[positions[train_end]+5]
        if train_label_end > seen:
            raise ValueError("Parent training labels exceed parent evidence")
        from tasks.resumption import resume_calendar_path
        resume_calendar_path(self.calendar_path, metadata["binding"])
        if metadata["request"].get("kind") == KIND:
            # Continued models may have non-contiguous training days.
            replay = {date.fromisoformat(d) for d in native["comparison"]["new"]}
            if not replay or not replay <= positions.keys() or max(replay) != train_end or min(replay) < train_start:
                raise ValueError("Parent continuation replay proof unavailable")
        elif metadata["request"].get("operation") in {"train", "import"}:
            # The sealed native pipeline uses chronological interval splits,
            # exact 5-session labels and a date-only purge at its split boundary.
            replay = {d for d in opened if train_start <= d <= train_end}
        else:
            raise ValueError("Parent training replay proof unavailable")
        later = [d for d in opened if seen < d <= cutoff]
        if len(later) < 45 or not replay:
            raise ValueError("Continuation needs more completed trading sessions")
        parent = dict(history, seen_through=seen, train_label_end=train_label_end, input_start=min(replay))
        lookup = dict(kind="model_workflow", operation="predict", algorithm="lightgbm", model_id=id,
                      start_date=str(parent["input_start"]), end_date=str(cutoff), feature_build_id=request["feature_build_id"])
        from workflows.contracts import normalize_operation
        lookup = normalize_operation(lookup)
        binding = self.workflow.bind(lookup)
        if binding["model_manifest_sha256"] != row["manifest_sha256"] or binding["data"]["calendar_sha256"] != digest(dates):
            raise SourceChanged("Continuation parent or calendar changed")
        frozen = metadata["binding"]["data"]
        verify_lifecycle_prefix(frozen.get("lifecycle", {}).get("stocks", {}),
                                binding["data"]["lifecycle"]["stocks"], frozen["stock_codes"], str(train_label_end))
        binding.update(parent_revision=release["revision"], parent_proof_sha256=release["proof_sha256"],
                       feature_names=native["feature_names"], score_units=native["score_units"],
                       continuation_sha256=engine_identity(), input_start=lookup["start_date"], input_cutoff=lookup["end_date"])
        with self.engine.connect() as connection:
            assert_current(connection, request, binding)
        return binding, lookup, parent, replay, cutoff

    def collect(self, value, expected, *, emit=None, activity=None):
        binding, lookup, parent, replay, cutoff = self.prepare(value)
        if binding != expected:
            raise SourceChanged("Continuation inputs changed")
        from tasks.market_source import MarketSource
        from workflows.source import native_request
        codes = binding["data"]["stock_codes"]
        reader = MarketSource(self.engine, self.calendar_path, pool=binding.get("registry_pool"), frozen_codes=codes)
        reader.activity = activity
        stocks = reader.iter_stocks(dict(request=native_request(lookup), binding=binding["data"]))
        rows, windows, prefix = build_samples(stocks,
            codes=codes, names=binding["feature_names"], parent=parent, replay_days=replay, cutoff=cutoff, emit=emit)
        with self.engine.connect() as connection:
            assert_current(connection, value, binding)
        return rows, windows, prefix

    def bind(self, request):
        # Read-only; does not load a trainer, persist an artifact or acquire data.
        return self.prepare(request)[0]
