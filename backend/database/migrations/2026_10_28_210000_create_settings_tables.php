<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * Versioned settings, one row per env (brief §5, §14), their history, and the audit log
 * every write in the app records into (brief §16).
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('settings', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', ['demo', 'live'])->unique();
            $table->unsignedInteger('version');
            $table->jsonb('data');
            // Hot columns mirrored from data for cheap reads; data is authoritative.
            $table->string('risk_profile', 16);
            $table->unsignedSmallInteger('autonomy_level');
            $table->decimal('max_loss_per_trade_pct', 12, 6);
            $table->decimal('max_position_pct', 12, 6);
            $table->unsignedSmallInteger('max_leverage');
            $table->timestampsTz(6);
        });

        Schema::create('settings_history', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->foreignUuid('settings_id')->constrained('settings')->cascadeOnDelete();
            $table->unsignedInteger('version');
            $table->jsonb('data');
            $table->jsonb('diff');
            $table->foreignUuid('changed_by')->nullable()->constrained('users')->nullOnDelete();
            $table->timestampTz('created_at', 6);

            $table->unique(['settings_id', 'version']);
        });

        Schema::create('audit_log', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->foreignUuid('user_id')->nullable()->constrained('users')->nullOnDelete();
            $table->string('action', 64);
            $table->string('subject_type', 64);
            $table->string('subject_id', 64)->nullable();
            $table->jsonb('before')->nullable();
            $table->jsonb('after')->nullable();
            $table->string('ip', 45)->nullable();
            $table->timestampTz('created_at', 6);

            $table->index(['subject_type', 'subject_id']);
            $table->index('created_at');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('audit_log');
        Schema::dropIfExists('settings_history');
        Schema::dropIfExists('settings');
    }
};
