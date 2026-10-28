<?php

declare(strict_types=1);

namespace App\Support;

use App\Models\AuditLog;
use App\Models\User;
use Illuminate\Http\Request;

/**
 * Records one audit_log row for a write (brief §1.9, §16). Every feature that changes state
 * calls this inside the same transaction as the write.
 *
 * Never pass secrets in $before/$after: send masked values (e.g. last 4 characters) instead.
 */
final class Auditable
{
    public function __construct(private readonly Request $request) {}

    /**
     * @param  string  $action  dotted verb, e.g. "settings.updated", "connection.created"
     * @param  string  $subjectType  short table-style name, e.g. "settings", "connections"
     * @param  array<string, mixed>|null  $before
     * @param  array<string, mixed>|null  $after
     * @param  User|null  $user  defaults to the authenticated user of the current request
     */
    public function record(
        string $action,
        string $subjectType,
        ?string $subjectId,
        ?array $before = null,
        ?array $after = null,
        ?User $user = null,
    ): AuditLog {
        $actor = $user ?? $this->request->user();

        return AuditLog::query()->create([
            'user_id' => $actor instanceof User ? $actor->id : null,
            'action' => $action,
            'subject_type' => $subjectType,
            'subject_id' => $subjectId,
            'before' => $before,
            'after' => $after,
            'ip' => $this->request->ip(),
        ]);
    }
}
