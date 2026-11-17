from pathlib import Path

import pyxdf


def saved_xdf_snapshot(folder):
    """Map each XDF path to its size and modification time."""
    result = {}
    folder = Path(folder)
    if folder.is_dir():
        for path in folder.rglob("*.xdf"):
            stat = path.stat()
            result[str(path.resolve())] = (stat.st_size, stat.st_mtime_ns)
    return result


def new_xdfs_since_snapshot(folder, previous_snapshot):
    after = saved_xdf_snapshot(folder)
    created_xdfs = [(Path(name), info[1]) for name, info in after.items() if name not in previous_snapshot]
    created_xdfs.sort(key=lambda item: item[1], reverse=True)
    return [path for path, _ in created_xdfs]


def parse_xdf_counts(filename):
    try:
        streams, _ = pyxdf.load_xdf(str(filename), verbose=False)
    except Exception as exc:
        print(f"Error loading XDF file '{filename}': {exc}")
        return {}

    counts = {}
    for index, stream in enumerate(streams):
        info = stream.get("info", {})
        name_list = info.get("name")
        if isinstance(name_list, list) and name_list and name_list[0]:
            name = name_list[0]
        else:
            name = f"unnamed_stream_{index}"

        time_stamps = stream.get("time_stamps")
        if time_stamps is None:
            continue

        counts[name] = counts.get(name, 0) + len(time_stamps)
    return counts
