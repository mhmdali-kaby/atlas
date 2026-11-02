<?php

declare(strict_types=1);

namespace App\Support\Telegram;

use App\Models\AuditLog;
use App\Models\Proposal;
use App\Models\TelegramConnection;
use App\Models\User;
use App\Support\Auditable;
use App\Support\Proposals\ApprovalOutcome;
use App\Support\Proposals\ProposalApprovalService;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;

/**
 * One getUpdates round for atlas:telegram-poll (ATL-215, decision #254 (3)).
 *
 * - The stored update_offset is the last update_id taken. It is written, under a row lock, before
 *   the update is handled, and any update_id at or below it is skipped, so a restart (or Telegram
 *   re-sending an unconfirmed batch) never handles an update twice. At-most-once is the safe side
 *   for an approval button.
 * - Only a callback from the owner's own private chat counts (ATL-221): callback_query.from.id must
 *   equal the stored chat id and message.chat.type must be "private". Anyone else — another member
 *   of a group the bot was added to, or a forwarded/foreign press — is refused, answered "expired",
 *   and audited as telegram.callback_refused / foreign_user. Nothing about the proposal changes.
 * - A malformed or wrongly signed callback, one past its expiry, or one for a proposal that is no
 *   longer waiting is answered "expired" and changes nothing.
 * - Approve on a live proposal is refused with "approve in Atlas" and audited as
 *   telegram.callback_refused / live_approve_in_atlas (ATL-238); Reject on it works.
 * - A valid press calls ProposalApprovalService (the same path as the web Approve / Reject) with
 *   actor "telegram", answers the callback and edits the message to show the outcome.
 */
final class TelegramPoller
{
    /** Errors from the approval service that mean "this button is stale". */
    private const STALE = ['approval_expired', 'not_awaiting_owner', 'not_rejectable'];

    public function __construct(private readonly ProposalApprovalService $approvals, private readonly Auditable $audit) {}

    /**
     * @return int the number of updates taken (handled or ignored)
     *
     * @throws TelegramException when getUpdates fails
     */
    public function pollOnce(int $timeout): int
    {
        $connection = TelegramConnection::current();
        if ($connection === null) {
            return 0;
        }
        $client = TelegramClient::for($connection);
        $offset = $connection->update_offset;
        $updates = $client->getUpdates($offset === null ? null : $offset + 1, $timeout);
        usort($updates, static fn (array $a, array $b): int => self::updateId($a) <=> self::updateId($b));

        $taken = 0;
        foreach ($updates as $update) {
            $id = self::updateId($update);
            if ($id < 0 || ! $this->take($connection->id, $id)) {
                continue;
            }
            $taken++;
            try {
                $this->handle($client, $connection, $update);
            } catch (\Throwable $e) {
                // Already taken: never retried. The exception class only (a TelegramException is scrubbed anyway).
                Log::warning('atlas.telegram.update_failed', ['update_id' => $id, 'exception' => $e::class]);
            }
        }

        return $taken;
    }

    /** Store $id as the offset unless it was already taken. */
    private function take(string $connectionId, int $id): bool
    {
        return DB::transaction(function () use ($connectionId, $id): bool {
            $row = TelegramConnection::query()->lockForUpdate()->find($connectionId);
            if ($row === null || ($row->update_offset !== null && $id <= $row->update_offset)) {
                return false;
            }
            $row->forceFill(['update_offset' => $id])->save();

            return true;
        });
    }

    /**
     * @param  array<string, mixed>  $update
     */
    private function handle(TelegramClient $client, TelegramConnection $connection, array $update): void
    {
        $query = $update['callback_query'] ?? null;
        if (! is_array($query) || ! is_string($query['id'] ?? null)) {
            return;
        }
        $locale = TelegramMessages::ownerLocale();
        $expired = (string) __('telegram.callback.expired', [], $locale);

        $message = is_array($query['message'] ?? null) ? $query['message'] : [];
        $chat = is_array($message['chat'] ?? null) ? $message['chat'] : [];
        $from = is_array($query['from'] ?? null) ? $query['from'] : [];
        $fromId = $from['id'] ?? null;
        $isOwner = (is_int($fromId) || is_string($fromId)) && hash_equals($connection->chat_id, (string) $fromId);
        $isPrivate = ($chat['type'] ?? null) === 'private';
        if (! $isOwner || ! $isPrivate) {
            Log::info('atlas.telegram.foreign_user_ignored', ['update_id' => self::updateId($update)]);
            $this->refused(null, 'foreign_user');
            $client->answerCallbackQuery($query['id'], $expired);

            return;
        }

        $data = is_string($query['data'] ?? null) ? CallbackData::decode($query['data']) : null;
        if ($data === null) {
            $this->refused(null, 'bad_signature');
            $client->answerCallbackQuery($query['id'], $expired);

            return;
        }
        $proposal = Proposal::query()->find($data['proposal_id']);
        if ($proposal === null || Carbon::now()->getTimestamp() >= $data['expires_unix']) {
            $this->refused($data['proposal_id'], $proposal === null ? 'unknown_proposal' : 'expired');
            $client->answerCallbackQuery($query['id'], $expired);

            return;
        }

        // ATL-238, decision #255 (7): a live proposal is approved only in Atlas, behind the second
        // "real money" confirmation. Telegram Reject still works.
        if ($data['action'] === CallbackData::APPROVE && $proposal->env === 'live') {
            $this->refused($proposal->id, 'live_approve_in_atlas');
            $client->answerCallbackQuery($query['id'], mb_substr((string) __('telegram.callback.approve_in_atlas', [], $locale), 0, 200));

            return;
        }

        $owner = User::query()->orderBy('created_at')->first();
        $outcome = $data['action'] === CallbackData::APPROVE
            ? $this->approvals->approve($proposal, 'telegram', $owner)
            : $this->approvals->reject($proposal, 'telegram', (string) __('telegram.reject_reason', [], $locale), $owner);

        if ($outcome->error !== null && in_array($outcome->error, self::STALE, true)) {
            $this->refused($proposal->id, $outcome->error);
            $client->answerCallbackQuery($query['id'], $expired);

            return;
        }

        $client->answerCallbackQuery($query['id'], $this->answer($data['action'], $outcome, $locale));
        $messageId = $message['message_id'] ?? null;
        if (is_int($messageId)) {
            $original = is_string($message['text'] ?? null) ? $message['text'] : '';
            $client->editMessageText($connection->chat_id, $messageId, trim($original."\n\n".$this->outcomeLine($data['action'], $outcome, $locale)));
        }
    }

    private function answer(string $action, ApprovalOutcome $outcome, string $locale): string
    {
        $reason = $outcome->error === null ? '' : (string) __("proposals.{$outcome->error}", [], $locale);
        $key = $action === CallbackData::APPROVE
            ? ($outcome->ok ? 'telegram.callback.approved' : 'telegram.callback.approve_failed')
            : ($outcome->ok ? 'telegram.callback.rejected' : 'telegram.callback.reject_failed');

        return mb_substr((string) __($key, ['reason' => $reason], $locale), 0, 200);
    }

    private function outcomeLine(string $action, ApprovalOutcome $outcome, string $locale): string
    {
        $time = TelegramMessages::baghdadTime(Carbon::now());
        $reason = $outcome->error === null ? '' : (string) __("proposals.{$outcome->error}", [], $locale);
        $key = $action === CallbackData::APPROVE
            ? ($outcome->ok ? 'telegram.outcome.approved' : 'telegram.outcome.approve_failed')
            : 'telegram.outcome.rejected';

        return (string) __($key, ['time' => $time, 'reason' => $reason], $locale);
    }

    /** An audit row for a refused press from the owner's chat (nothing else changes). */
    private function refused(?string $proposalId, string $why): AuditLog
    {
        return $this->audit->record('telegram.callback_refused', 'proposals', $proposalId, null, ['actor' => 'telegram', 'reason' => $why]);
    }

    /**
     * @param  array<mixed>  $update
     */
    private static function updateId(array $update): int
    {
        $id = $update['update_id'] ?? null;

        return is_int($id) ? $id : -1;
    }
}
