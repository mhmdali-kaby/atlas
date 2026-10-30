<?php

declare(strict_types=1);

// Proposals API messages (ATL-162). Keys match the `error` slug in the response or a validation code.
return [
    'not_awaiting_owner' => 'This proposal is not waiting for your approval.',
    'approval_expired' => 'The approval window for this proposal has closed.',
    'risk_check_missing' => 'This proposal has no passing risk check to approve.',
    'settings_changed' => 'Your settings changed after the risk check. Edit the proposal to re-run the check, or wait for the next cycle.',
    'not_rejectable' => 'Only a proposal waiting for approval or shown as advice can be rejected.',
    'not_editable' => 'Only an entry proposal can be edited.',
    'execution_refused' => 'The trade was not sent. The reason is shown with the proposal.',
    'execution_failed' => 'OKX did not accept the order, or its stop could not be confirmed. See the War Room for details.',
    'trading_core_error' => 'The trading service could not carry out this action. Try again.',
    'trading_core_unavailable' => 'The trading service is not running. Start Atlas and try again.',
    'risk_engine_unavailable' => 'The Risk Engine could not check the edited proposal. Your original proposal is unchanged.',
    'status_unknown' => 'Unknown proposal status ":status".',
    'stop_not_positive' => 'The stop must be above zero.',
    'stop_widened' => 'The stop can only move closer to the entry, never further away.',
    'stop_wrong_side' => 'The stop must stay on the losing side of the entry.',
    'target_wrong_side' => 'Every target must be on the winning side of the entry.',
    'targets_over_100' => 'The targets together cannot close more than 100% of the position.',
    'ask_team_message' => '@:handle :question

(About the :side:inst proposal, version :version.)',
];
