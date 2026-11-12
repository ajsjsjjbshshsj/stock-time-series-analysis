"""Owned-worker continuation and independent native candidate verification."""
from datetime import date
import time

from tasks import resources
from tasks.artifacts import read_json, task_digest, write_json
from tasks.contracts import TaskFailure
from tasks.market_profile import bounded_profile, parameters
from tasks.source import SourceChanged
from workflows.artifacts import ArtifactStore
from workflows.training import verify_native
from .source import ContinuationSource


def verify_prefix(parent, candidate, rounds):
    old, new = parent.current_iteration(), candidate.current_iteration()
    if (old < 1 or new != old + rounds or parent.num_trees() != old or candidate.num_trees() != new
            or parent.num_model_per_iteration() != 1 or candidate.num_model_per_iteration() != 1):
        raise ValueError("Incorrect LightGBM continuation rounds")
    if parent.feature_name() != candidate.feature_name():
        raise ValueError("Continuation feature order changed")
    if parent.dump_model()["tree_info"] != candidate.dump_model(num_iteration=old)["tree_info"]:
        raise ValueError("Continuation rewrote a parent tree")
    return old, new


def append_trees(parent, values, labels, *, rounds, emit):
    import lightgbm as lgb
    params = parameters(bounded_profile(), "regression")
    dataset = lgb.Dataset(values, label=labels, feature_name=parent.feature_name(), params=params)

    def progress(env):
        emit("progress", phase="TRAIN", completed=env.iteration-env.begin_iteration+1, total=rounds)

    candidate = lgb.train(params, dataset, init_model=parent, num_boost_round=rounds, callbacks=[progress])
    verify_prefix(parent, candidate, rounds)
    return candidate


def fit_candidate(task, folder, parent, rows, windows, emit):
    from analysis.market_validation import ranking_metrics, within_day_ranks
    request, binding = task["request"], task["binding"]
    names = binding["feature_names"]
    replay, new, comparison = ({date.fromisoformat(d) for d in windows[key]}
                               for key in ("replay", "new", "comparison"))
    train, test = (rows.loc[rows.signal_date.isin(days)] for days in (replay | new, comparison))
    train_x, compare_x = train[names].to_numpy(), test[names].to_numpy()
    train_y, compare_y = train.label.to_numpy(), test.label.to_numpy()
    train_dates = train.signal_date.to_numpy(dtype="datetime64[D]").astype("int64")
    compare_dates = test.signal_date.to_numpy(dtype="datetime64[D]").astype("int64")
    rank = binding["score_units"] == "within_day_centered_rank"
    if rank:
        train_y = within_day_ranks(train_y, train_dates)
    compare_target = within_day_ranks(compare_y, compare_dates) if rank else compare_y
    emit("stage", stage="TRAIN")
    started = time.monotonic()
    model = append_trees(parent, train_x, train_y, rounds=request["additional_rounds"], emit=emit)
    training_seconds = time.monotonic()-started
    scores = {}
    for name, booster in (("parent", parent), ("candidate", model)):
        pred = booster.predict(compare_x, num_threads=2)
        report = ranking_metrics(pred, compare_target, compare_dates)
        report.pop("daily", None)
        scores[name] = report
    old, total = verify_prefix(parent, model, request["additional_rounds"])
    model.save_model(str(folder / "model.txt"))
    probe = compare_x[:16]
    write_json(folder / "reload.json", dict(values=probe.tolist(),
        predictions=model.predict(probe, num_threads=2).tolist()))
    codes = binding["data"]["stock_codes"]
    coverage = {}
    for name, days in (("replay", replay), ("new", new), ("comparison", comparison)):
        counts = rows.loc[rows.signal_date.isin(days)].groupby("ts_code").size()
        coverage[name] = {code: len(days)-int(counts.get(code, 0)) for code in codes}
    write_json(folder / "coverage.json", dict(missing_sessions_by_stock=coverage))
    return dict(algorithm="lightgbm", ts_code=None, stock_codes=None, feature_names=names,
                feature_count=len(names), pool_count=binding["pool_count"], score_units=binding["score_units"],
                horizon=5, holding_sessions=5, train_start=str(min(replay | new)), train_end=str(max(new)),
                label_definition="close[t+5]/close[t]-1" + ("; within-day centered ranks" if rank else ""),
                seen_through=windows["seen_through"], rounds=total, previous_rounds=old,
                additional_rounds=request["additional_rounds"], parent_model_id=request["parent_model_id"],
                parent_manifest_sha256=binding["model_manifest_sha256"], comparison=windows,
                metrics=scores, training_seconds=round(training_seconds, 6),
                stocks_missing_sessions={name: sum(n > 0 for n in counts.values())
                                         for name, counts in coverage.items()})


def run_continuation(task, root, db, emit, owner_guard, reserve):
    from tasks.telemetry import ActivityReporter
    store = ArtifactStore(root)
    folder = store.path(task["id"])
    resources.preflight(folder, bounded_profile())
    emit("stage", stage="PREFLIGHT")
    source = ContinuationSource(db.engine)
    started = time.monotonic()
    emit("stage", stage="INPUT")
    bound = task["binding"]
    try:
        rows, windows, history = source.collect(task["request"], bound, emit=emit, activity=ActivityReporter(emit))
    except SourceChanged:
        raise
    except ValueError:
        raise TaskFailure("CONTINUATION_NOT_READY") from None
    owner_guard()
    parent = verify_native(store.path(task["request"]["parent_model_id"]), "lightgbm")
    if parent.feature_name() != bound["feature_names"]:
        raise SourceChanged("Continuation feature order changed")
    reserve(windows)
    folder.mkdir(parents=True, exist_ok=False)
    native = fit_candidate(task, folder, parent, rows, windows, emit)
    emit("stage", stage="VERIFY")
    native["elapsed_seconds"] = round(time.monotonic()-started, 6)
    write_json(folder / "native.json", native)
    reloaded = verify_native(folder, "lightgbm")
    verify_prefix(parent, reloaded, task["request"]["additional_rounds"])
    write_json(folder / "history.json", history)
    owner_guard()
    index = dict(artifact_kind="model", algorithm="lightgbm", model_id=task["id"],
                 created_at=int(time.time()), title=f"lightgbm · continuation · {native['train_end']}",
                 metadata=dict(native, status="COMPLETE", operation="continue"))
    proof = store.seal(task["id"], dict(schema_version=1, task_sha256=task_digest(task), index=index,
        request=task["request"], binding=bound, native=native, published=False))
    emit("stage", stage="SAVE")
    return proof


def verify_candidate(task, root, windows):
    store = ArtifactStore(root)
    result = store.verify(task["id"], task_sha256=task_digest(task))
    folder = store.path(task["id"])
    metadata = read_json(folder / "metadata.json", max_bytes=8*1024*1024)
    id = task["request"]["parent_model_id"]
    if (metadata["request"] != task["request"] or metadata["binding"] != task["binding"]
            or metadata["published"] is not False or result["index"]["artifact_kind"] != "model"
            or store.verify(id)["manifest_sha256"] != task["binding"]["model_manifest_sha256"]):
        raise ValueError("Continuation seal or parent mismatch")
    parent = verify_native(store.path(id), "lightgbm")
    candidate = verify_native(folder, "lightgbm")
    old, total = verify_prefix(parent, candidate, task["request"]["additional_rounds"])
    native = read_json(folder / "native.json", max_bytes=8*1024*1024)
    if (metadata["native"] != native or native["parent_model_id"] != id
            or native["parent_manifest_sha256"] != task["binding"]["model_manifest_sha256"]
            or native["comparison"] != windows or native["previous_rounds"] != old
            or native["rounds"] != total or native["seen_through"] != windows["seen_through"]):
        raise ValueError("Continuation lineage mismatch")
    if result["index"]["metadata"] != dict(native, status="COMPLETE", operation="continue"):
        raise ValueError("Continuation candidate index differs from native evidence")
    coverage = read_json(folder / "coverage.json", max_bytes=2*1024*1024)["missing_sessions_by_stock"]
    codes = set(task["binding"]["data"]["stock_codes"])
    for name in ("replay", "new", "comparison"):
        missing, days = coverage[name], len(windows[name])
        if (set(missing) != codes or any(type(n) is not int or not 0 <= n <= days for n in missing.values())
                or sum(n > 0 for n in missing.values()) != native["stocks_missing_sessions"][name]
                or sum(missing.values()) != days*len(codes)-sum(windows["pool_coverage"][name].values())):
            raise ValueError("Continuation per-stock coverage differs")
    return result
