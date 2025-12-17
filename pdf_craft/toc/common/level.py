from typing import Generator


_MAX_LEVELS = 6


def calculate_levels(heights: list[float]) -> list[int]:
    if len(heights) <= 1:
        return [1] * len(heights)

    unique_heights = sorted(set(heights), reverse=True)
    max_levels = min(_MAX_LEVELS, len(unique_heights))

    if len(unique_heights) <= max_levels:
        height_to_level = {h: i + 1 for i, h in enumerate(unique_heights)}
    else:
        clustered_heights = list(_cluster_heights(unique_heights, max_levels))
        height_to_level = {}
        for i, cluster in enumerate(clustered_heights):
            for h in cluster:
                height_to_level[h] = i + 1

    return [height_to_level[h] for h in heights]


def _cluster_heights(heights: list[float], max_clusters: int) -> Generator[list[float], None, None]:
    if len(heights) <= max_clusters:
        for h in heights:
            yield [h]
        return

    gaps = [(heights[i] - heights[i + 1], i) for i in range(len(heights) - 1)]
    gaps.sort(reverse=True)
    split_indices = sorted([idx + 1 for _, idx in gaps[:max_clusters - 1]])

    start = 0
    for split_idx in split_indices:
        yield heights[start:split_idx]
        start = split_idx
    yield heights[start:]
