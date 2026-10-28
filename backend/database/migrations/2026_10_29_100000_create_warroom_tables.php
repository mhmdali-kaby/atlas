<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * War Room threads and messages (brief §5 warroom_messages, §12). A message persisted from a
 * Redis Streams event keeps that event's id in `event_id`, so it is stored at most once.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::create('warroom_threads', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', ['demo', 'live']);
            $table->enum('kind', ['main', 'instrument', 'agent']);
            // main: "main"; instrument: an instId such as BTC-USDT-SWAP; agent: an agent key such as historian.
            $table->string('key', 64);
            $table->string('title', 255);
            $table->timestampsTz(6);

            $table->unique(['env', 'kind', 'key']);
        });

        Schema::create('warroom_messages', function (Blueprint $table): void {
            $table->uuid('id')->primary();
            $table->enum('env', ['demo', 'live']);
            $table->foreignUuid('thread_id')->constrained('warroom_threads')->cascadeOnDelete();
            $table->uuid('event_id')->nullable()->unique();
            $table->enum('author_type', ['agent', 'human', 'system']);
            $table->string('author', 64);
            $table->string('kind', 32);
            $table->text('content_md');
            $table->jsonb('card')->nullable();
            $table->uuid('reply_to')->nullable();
            $table->text('reasoning_md')->nullable();
            $table->jsonb('tool_calls')->nullable();
            $table->unsignedInteger('tokens_in')->nullable();
            $table->unsignedInteger('tokens_out')->nullable();
            $table->decimal('cost_usd', 18, 6)->nullable();
            $table->timestampsTz(6);

            $table->index(['thread_id', 'created_at', 'id']);
        });

        // A self-reference needs the primary key to exist first.
        Schema::table('warroom_messages', function (Blueprint $table): void {
            $table->foreign('reply_to')->references('id')->on('warroom_messages')->nullOnDelete();
        });

        // Blueprint has no text[] column type.
        DB::statement("ALTER TABLE warroom_messages ADD COLUMN mentions text[] NOT NULL DEFAULT '{}'");
    }

    public function down(): void
    {
        Schema::dropIfExists('warroom_messages');
        Schema::dropIfExists('warroom_threads');
    }
};
