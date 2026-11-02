<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * ATL-231, decision #255 (3): one row per `php artisan atlas:backup` run. Env-agnostic: a backup
 * is a pg_dump of the whole Atlas database, demo and live alike. A failed run keeps pg_dump's
 * error text; an ok run its file's size.
 */
return new class extends Migration
{
    public const STATUSES = ['ok', 'failed'];

    public function up(): void
    {
        Schema::create('backups', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            // Absolute path of the dump file (written, or the one the failed run tried to write).
            $table->text('path');
            $table->unsignedBigInteger('bytes')->nullable();
            $table->timestampTz('started_at', 6);
            $table->timestampTz('finished_at', 6);
            $table->enum('status', self::STATUSES);
            $table->text('error')->nullable();
            $table->timestampsTz(6);

            $table->index(['status', 'finished_at']);
            $table->index('started_at');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('backups');
    }
};
