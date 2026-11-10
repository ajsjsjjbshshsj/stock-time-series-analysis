"""Selection independent of scores, with the full frozen pool as denominator."""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class WindowSelection:
    replay: tuple[date, ...]
    new: tuple[date, ...]
    comparison: tuple[date, ...]
    seen_through: date
    coverage: dict

    def evidence(self):
        return dict(replay=list(map(str, self.replay)), new=list(map(str, self.new)),
                    comparison=list(map(str, self.comparison)), seen_through=str(self.seen_through),
                    pool_coverage=self.coverage)


def select_windows(samples, *, seen_through, cutoff, pool_count, replay_days):
    if type(pool_count) is not int or pool_count < 1 or seen_through >= cutoff:
        raise ValueError("Continuation source dates or pool unavailable")
    if samples.duplicated(["signal_date", "ts_code"]).any():
        raise ValueError("Duplicate continuation sample")
    if (samples["label_date"] <= samples["signal_date"]).any() or (samples["label_date"] > cutoff).any():
        raise ValueError("Unmatured or invalid continuation label")
    by_day = samples.groupby("signal_date", sort=True).agg(count=("ts_code", "size"), latest=("label_date", "max"))
    if (by_day["count"] > pool_count).any():
        raise ValueError("Continuation sample outside frozen pool")
    qualified = {day: (int(row["count"]), row["latest"]) for day, row in by_day.iterrows()
                 if row["count"] * 5 >= pool_count * 4}
    later = [day for day in qualified if day > seen_through]
    if len(later) < 20:
        raise ValueError("Continuation needs 20 comparison sessions")
    comparison = tuple(later[-20:])
    first = comparison[0]
    new = tuple(day for day in later if qualified[day][1] < first)[-60:]
    if len(new) < 20:
        raise ValueError("Continuation needs 20 new training sessions after purging")
    replay = tuple(day for day in qualified if day in replay_days and qualified[day][1] <= seen_through)[-20:]
    if not replay:
        raise ValueError("Continuation needs verified parent training replay")
    return WindowSelection(replay, new, comparison, max(qualified[d][1] for d in comparison),
                           {name: {str(day): qualified[day][0] for day in days}
                            for name, days in (("replay", replay), ("new", new), ("comparison", comparison))})
