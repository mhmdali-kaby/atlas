<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * Exchange API connections (brief §1.6, §5, §14.2). api_key, api_secret and passphrase hold
 * Laravel `encrypted`-cast ciphertext (Crypt::encryptString with APP_KEY); trading_core decrypts
 * them itself with atlas_shared.laravel_crypt (decision #235).
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('connections', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->string('exchange', 16)->default('okx');
            $table->enum('env', ['demo', 'live']);
            $table->string('label', 100);
            $table->text('api_key');
            $table->text('api_secret');
            $table->text('passphrase');
            // Filled by Test connection (ATL-8); reset to null whenever a secret changes.
            $table->jsonb('permissions')->nullable();
            $table->string('account_mode', 32)->nullable();
            $table->string('position_mode', 32)->nullable();
            $table->string('uid_last4', 4)->nullable();
            $table->timestampTz('last_verified_at', 6)->nullable();
            $table->text('last_test_error')->nullable();
            $table->timestampsTz(6);

            $table->unique(['exchange', 'env']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('connections');
    }
};
