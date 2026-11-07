<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * ATL-65: when ingest published the cluster's `news.alert`. One alert per story: ingest claims the
 * cluster with `UPDATE … SET alerted_at = now() WHERE alerted_at IS NULL`, so later items of the
 * same story never alert again.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('news_clusters', function (Blueprint $table): void {
            $table->timestampTz('alerted_at', 6)->nullable();
        });
    }

    public function down(): void
    {
        Schema::table('news_clusters', function (Blueprint $table): void {
            $table->dropColumn('alerted_at');
        });
    }
};
