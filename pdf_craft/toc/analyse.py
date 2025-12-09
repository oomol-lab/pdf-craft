from dataclasses import dataclass
from typing import Iterable
from .item import TocItem


@dataclass
class RawChapter:
    id: int
    title: str
    det: list[tuple[int, int, int, int]]


def analyse_toc(chapters: Iterable[RawChapter]) -> list[TocItem]:
    """
    Analyze table of contents items and build a hierarchical tree structure.

    This function implements a similar algorithm to marker's TOC processing:
    1. Calculate heading heights from bounding boxes
    2. Cluster headings by height to determine heading levels
    3. Build a tree structure based on hierarchical levels

    Args:
        chapters: List of RawChapter objects containing chapter information

    Returns:
        List of top-level TocItem objects with hierarchical children
    """
    if not chapters:
        return []

    # Calculate heading heights for each chapter
    heading_info: list[tuple[RawChapter, str, float]] = []
    for chapter in chapters:
        if not chapter.det:
            continue

        # Calculate average height across all bounding boxes (for multi-line titles)
        heights = [det[3] - det[1] for det in chapter.det]  # y2 - y1
        avg_height = sum(heights) / len(heights) if heights else 0

        if avg_height > 0:
            heading_info.append((chapter, chapter.title, avg_height))

    if not heading_info:
        return []

    # Assign heading levels based on height clustering
    heading_levels = _assign_heading_levels(heading_info)

    # Build tree structure
    return _build_toc_tree(heading_info, heading_levels)


def _assign_heading_levels(heading_info: list[tuple[RawChapter, str, float]]) -> list[int]:
    """
    Assign heading levels to each heading based on height clustering.
    Larger heights get lower level numbers (1 is the highest level).
    """
    if len(heading_info) <= 1:
        return [1] * len(heading_info)

    heights = [h for _, _, h in heading_info]

    # Use a simple approach: sort unique heights and assign levels
    # This is simpler than K-means but works well for most documents
    unique_heights = sorted(set(heights), reverse=True)

    # Create a mapping from height to level
    # We'll use at most 6 levels (h1-h6 in markdown)
    max_levels = min(6, len(unique_heights))

    # Group similar heights together
    if len(unique_heights) <= max_levels:
        # Simple case: each unique height gets its own level
        height_to_level = {h: i + 1 for i, h in enumerate(unique_heights)}
    else:
        # More complex: need to cluster similar heights
        # Use threshold-based clustering
        clustered_heights = _cluster_heights(unique_heights, max_levels)
        height_to_level = {}
        for i, cluster in enumerate(clustered_heights):
            for h in cluster:
                height_to_level[h] = i + 1

    # Assign levels to each heading
    return [height_to_level[h] for _, _, h in heading_info]


def _cluster_heights(heights: list[float], max_clusters: int) -> list[list[float]]:
    """
    Cluster heights into at most max_clusters groups.
    Heights should be sorted in descending order.
    """
    if len(heights) <= max_clusters:
        return [[h] for h in heights]

    # Calculate gaps between consecutive heights
    gaps = [(heights[i] - heights[i + 1], i) for i in range(len(heights) - 1)]

    # Sort by gap size (descending)
    gaps.sort(reverse=True)

    # Take the largest gaps as cluster boundaries
    split_indices = sorted([idx + 1 for _, idx in gaps[:max_clusters - 1]])

    # Build clusters
    clusters = []
    start = 0
    for split_idx in split_indices:
        clusters.append(heights[start:split_idx])
        start = split_idx
    clusters.append(heights[start:])

    return clusters


def _build_toc_tree(
    heading_info: list[tuple[RawChapter, str, float]],
    heading_levels: list[int]
) -> list[TocItem]:
    """
    Build a hierarchical tree structure from headings and their levels.
    """
    if not heading_info:
        return []

    root_items: list[TocItem] = []
    stack: list[tuple[int, TocItem]] = []  # (level, item)

    for (chapter, title, _), level in zip(heading_info, heading_levels):
        new_item = TocItem(id=chapter.id, title=title, children=[])

        # Pop items from stack that are at the same or deeper level
        while stack and stack[-1][0] >= level:
            stack.pop()

        # Add to parent or root
        if stack:
            # Add as child of the last item in stack
            parent_item = stack[-1][1]
            parent_item.children.append(new_item)
        else:
            # Add as root item
            root_items.append(new_item)

        # Push current item onto stack
        stack.append((level, new_item))

    return root_items
