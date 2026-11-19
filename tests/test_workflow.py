"""What the CI workflows promise about their own runs."""

import pathlib

ROOT = pathlib.Path(__file__).parent.parent
#: A pull request's runs share one group; every other run is a group of its own.
OWN_GROUP = (
    "group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}"
)
ONLY_PULL_REQUESTS = "cancel-in-progress: ${{ github.event_name == 'pull_request' }}"


def test_a_new_push_stops_only_a_pull_request_s_stale_run():
    """A pull request's older run is waste once it has a newer one, so it is cancelled.

    Every other run, on main, a tag or by hand, is the record of what landed. GitHub keeps only
    one waiting run per group, so with one group per branch a commit on main between two others
    got no run at all. Keyed by its own run id, each such run is its own group and runs to the end.
    """
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    assert workflows, "no workflows found"
    for workflow in workflows:
        text = workflow.read_text()
        assert OWN_GROUP in text, f"{workflow.name}: a run not from a pull request needs its own group"
        assert ONLY_PULL_REQUESTS in text, f"{workflow.name}: must cancel only pull-request runs"
