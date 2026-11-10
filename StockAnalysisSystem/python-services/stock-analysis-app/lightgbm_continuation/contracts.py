"""Only immutable registry IDs and a bounded round count are caller-controlled."""
from tasks.contracts import bound_integer, identity

KIND = "lightgbm_continue"
REQUIRED = frozenset(("kind", "parent_model_id", "feature_build_id"))


def normalize(value):
    if (not isinstance(value, dict) or not REQUIRED <= value.keys()
            or value.keys() - REQUIRED - {"additional_rounds"} or value["kind"] != KIND):
        raise ValueError("Invalid LightGBM continuation request")
    return dict(kind=KIND, parent_model_id=identity(value["parent_model_id"]),
                feature_build_id=identity(value["feature_build_id"]),
                additional_rounds=bound_integer(value.get("additional_rounds", 10), 1, 20))
