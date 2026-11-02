<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * ATL-235 (decisions #240, #255): when the last Test connection proved the key has no withdraw
 * permission. Written by trading_core's connection test; null when the key can withdraw, the test
 * failed, or a secret changed. The live pre-flight's "withdraw permission OFF verified" reads it.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('connections', function (Blueprint $table): void {
            $table->timestampTz('withdraw_off_verified_at', 6)->nullable()->after('last_verified_at');
        });
    }

    public function down(): void
    {
        Schema::table('connections', function (Blueprint $table): void {
            $table->dropColumn('withdraw_off_verified_at');
        });
    }
};
