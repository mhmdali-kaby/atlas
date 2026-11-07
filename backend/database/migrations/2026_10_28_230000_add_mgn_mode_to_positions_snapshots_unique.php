<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * OKX net mode lets one instrument hold a cross and an isolated position at the same time, so a
 * position snapshot is identified by its margin mode too (ATL-10 QA). NULLS NOT DISTINCT keeps
 * two rows with no margin mode from slipping past the key.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('positions_snapshots', function (Blueprint $table): void {
            $table->dropUnique(['env', 'inst_id', 'pos_side', 'taken_at']);
            $table->unique(['env', 'inst_id', 'pos_side', 'mgn_mode', 'taken_at'])->nullsNotDistinct();
        });
    }

    public function down(): void
    {
        Schema::table('positions_snapshots', function (Blueprint $table): void {
            $table->dropUnique(['env', 'inst_id', 'pos_side', 'mgn_mode', 'taken_at']);
            $table->unique(['env', 'inst_id', 'pos_side', 'taken_at']);
        });
    }
};
