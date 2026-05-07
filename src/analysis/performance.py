import bisect


class Series(list):
    def tolist(self):
        return list(self)


class MergedActivityData(dict):
    pass


def merge_activity_data(streams, glucose_values, glucose_seconds):
    time_stream = _time_stream(streams)
    interpolated = _linear_interpolate(time_stream, glucose_seconds, glucose_values)
    return MergedActivityData({"time": Series(time_stream), "bloodglucose": Series(interpolated)})


def _time_stream(streams):
    for stream in streams:
        if stream.get("type") == "time":
            return stream.get("data") or []
    raise ValueError("Could not find time stream")


def _linear_interpolate(time_stream, seconds, values):
    if not seconds or not values:
        return []

    result = []
    for t in time_stream:
        idx = bisect.bisect_left(seconds, t)
        if idx == 0:
            result.append(values[0])
        elif idx == len(seconds):
            result.append(values[-1])
        else:
            t0, t1 = seconds[idx - 1], seconds[idx]
            v0, v1 = values[idx - 1], values[idx]
            if t1 == t0:
                result.append(v1)
            else:
                weight = (t - t0) / (t1 - t0)
                result.append(v0 + weight * (v1 - v0))
    return result
