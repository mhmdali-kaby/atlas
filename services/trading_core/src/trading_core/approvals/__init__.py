"""The owner's approval of an ``awaiting_owner`` proposal (ATL-162, brief §12/§16).

``api``: signed ``POST /internal/proposals/{id}/execute`` re-runs the Risk Engine and, only when the
fresh check approves, hands it to the Executor. ``sweeper``: expires proposals at
``approval_expires_at`` and posts the halfway reminder.
"""
