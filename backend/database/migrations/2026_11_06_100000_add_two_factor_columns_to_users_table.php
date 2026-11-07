<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * ATL-245, decision #265 (1): optional TOTP. The secret and the recovery codes are Laravel
 * `encrypted` casts (ciphertext at rest); the recovery codes are bcrypt hashes inside that JSON.
 * two_factor_last_timestep is the 30 s step of the last accepted code, so a code cannot be replayed.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('users', function (Blueprint $table): void {
            $table->text('two_factor_secret')->nullable();
            $table->text('two_factor_recovery_codes')->nullable();
            $table->timestamp('two_factor_confirmed_at')->nullable();
            $table->bigInteger('two_factor_last_timestep')->nullable();
        });
    }

    public function down(): void
    {
        Schema::table('users', function (Blueprint $table): void {
            $table->dropColumn([
                'two_factor_secret',
                'two_factor_recovery_codes',
                'two_factor_confirmed_at',
                'two_factor_last_timestep',
            ]);
        });
    }
};
