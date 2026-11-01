<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * Owner notifications (brief §17, decision #254 (2), ATL-214). One row per bus envelope that is a
 * §17 event, written by atlas:bridge (App\Support\Notifications\Notifier). source_event_id is the
 * envelope id and is unique, so a replayed envelope never stores a second row.
 *
 * The title is an i18n key (title_key) plus params, never English text. params hold only the
 * allow-listed scalar fields of the event (never a connection key, secret or UID). channels records
 * which delivery switches were on when the row was stored (in_app, browser, telegram).
 */
return new class extends Migration
{
    public const ENVS = ['demo', 'live'];

    public const SEVERITIES = ['info', 'warning', 'critical'];

    public function up(): void
    {
        Schema::create('notifications', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', self::ENVS);
            $table->string('event', 64);
            $table->enum('severity', self::SEVERITIES);
            $table->string('title_key', 128);
            $table->jsonb('params')->default(DB::raw("'{}'::jsonb"));
            $table->string('link', 512)->nullable();
            $table->uuid('source_event_id')->unique();
            $table->jsonb('channels')->default(DB::raw("'{}'::jsonb"));
            $table->timestampTz('read_at', 6)->nullable();
            $table->timestampTz('created_at', 6);

            $table->index(['env', 'created_at', 'id']);
        });

        DB::statement('CREATE INDEX notifications_env_unread_idx ON notifications (env, created_at DESC, id DESC) WHERE read_at IS NULL');
    }

    public function down(): void
    {
        Schema::dropIfExists('notifications');
    }
};
