"""Read-only predecessor hypotheses within the target page's first JEV call."""

from .review_templates import review_question


def predecessor_requests(requests):
    for index, packet in requests:
        for name, check in packet["state"]["checks"].items():
            if not name.startswith("gap_"):
                continue
            candidates = check.get("preceding_source_candidates", [])
            sources = packet["state"].get("preceding_source_index", {})
            if (
                not candidates
                or len(candidates) > 12
                or any(sources[key]["page_index"] == index for key in candidates)
            ):
                return []
            jobs = []
            for number, key in enumerate(candidates):
                target = f"predecessor_candidate_{number}"
                spec = review_question("predecessor_candidate", name=target)
                value = dict(
                    candidate_key=key,
                    candidate=dict(
                        sources[key], text_end=sources[key]["text_end"][-80:]
                    ),
                    target_source_order=check["source_order"],
                    target_start=check["current_start"][:80],
                )
                jobs.append(
                    (
                        index,
                        dict(
                            questions={target: spec}, state=dict(checks={target: value})
                        ),
                    )
                )
            return jobs
    return []


def predecessor_hypotheses(jobs, scores):
    return [
        dict(packet["state"]["checks"][name], probability=scores[name])
        for _, packet in jobs
        for name in packet["questions"]
    ]
