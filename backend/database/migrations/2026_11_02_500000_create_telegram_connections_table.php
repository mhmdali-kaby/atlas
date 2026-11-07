<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * The owner's optional Telegram bot (brief §14.9, §17, decision #254 (3), ATL-215). At most one row
 * (`singleton` is unique and always true). bot_token and chat_id are Laravel `encrypted` casts and
 * are never returned or logged; the API shows their last 4 characters only.
 *
 * update_offset is the highest getUpdates update_id atlas:telegram-poll has taken, stored before
 * the update is handled, so a restart resumes after it and never handles an update twice. A new
 * bot token resets it to null.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('telegram_connections', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->boolean('singleton')->default(true)->unique();
            $table->text('bot_token');
            $table->text('chat_id');
            $table->string('bot_token_last4', 4);
            $table->string('chat_id_last4', 4);
            $table->bigInteger('update_offset')->nullable();
            $table->timestampTz('last_sent_at', 6)->nullable();
            $table->timestampTz('last_tested_at', 6)->nullable();
            $table->string('last_error', 300)->nullable();
            $table->timestampsTz(6);
        });

        DB::statement('ALTER TABLE telegram_connections ADD CONSTRAINT telegram_connections_singleton_check CHECK (singleton)');
    }

    public function down(): void
    {
        Schema::dropIfExists('telegram_connections');
    }
};
