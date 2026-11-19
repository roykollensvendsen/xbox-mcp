"""What the CI workflows promise about their own runs."""

import pathlib

ROOT = pathlib.Path(__file__).parent.parent
PER_REF = "group: ${{ github.workflow }}-${{ github.ref }}"
ONLY_PULL_REQUESTS = "cancel-in-progress: ${{ github.event_name == 'pull_request' }}"


def test_a_new_push_cancels_only_a_pull_request_s_older_run():
    """A pull request's older run is waste once it has a newer one, so it is cancelled.

    A run on main is the record of what landed, so once started it always finishes, and the
    next merge doesn't cancel it. GitHub keeps only one waiting run per group, so a commit on
    main between two others may still get no run.
    """
    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    assert workflows, "no workflows found"
    for workflow in workflows:
        text = workflow.read_text()
        assert PER_REF in text, f"{workflow.name}: no run group per ref"
        assert ONLY_PULL_REQUESTS in text, f"{workflow.name}: must cancel only pull-request runs"
