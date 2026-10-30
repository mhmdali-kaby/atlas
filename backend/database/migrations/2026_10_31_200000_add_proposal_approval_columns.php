<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * ATL-162, brief §12/§16: the owner's approve / reject / edit and the approval-timeout sweeper.
 *
 * proposals:
 * - status_reason: why the proposal reached its status, as a code: owner_rejected,
 *   approval_timeout, risk_rejected, revised (an owner Edit replaced it), execution_<reason>.
 * - owner_note: the owner's free-text reject reason.
 * - decided_by / decided_at: who closed it (owner, system) and when.
 * - approval_reminded_at: when the halfway reminder was posted, so it is posted once.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('proposals', function (Blueprint $table): void {
            $table->string('status_reason', 64)->nullable()->after('approval_expires_at');
            $table->text('owner_note')->nullable()->after('status_reason');
            $table->string('decided_by', 64)->nullable()->after('owner_note');
            $table->timestampTz('decided_at', 6)->nullable()->after('decided_by');
            $table->timestampTz('approval_reminded_at', 6)->nullable()->after('decided_at');
            $table->index(['env', 'status', 'approval_expires_at']);
        });
    }

    public function down(): void
    {
        Schema::table('proposals', function (Blueprint $table): void {
            $table->dropIndex(['env', 'status', 'approval_expires_at']);
            $table->dropColumn(['status_reason', 'owner_note', 'decided_by', 'decided_at', 'approval_reminded_at']);
        });
    }
};
