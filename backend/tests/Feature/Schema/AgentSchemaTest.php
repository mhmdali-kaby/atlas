<?php

declare(strict_types=1);

use Illuminate\Database\QueryException;
use Illuminate\Database\UniqueConstraintViolationException;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use Illuminate\Support\Str;

const AGENT_TABLES = [
    'agent_runs', 'agent_usage_daily', 'agent_states', 'prompt_versions',
    'research_reports', 'agent_notebook', 'journal_entries', 'agent_scores',
];

function agentTablesMigration(): object
{
    return require database_path('migrations/2026_10_29_400000_create_agent_tables.php');
}

/** @return array<string, list<string>> unique index name => sorted columns */
function uniqueIndexes(string $table): array
{
    $rows = DB::select(
        "SELECT i.relname AS name, array_to_string(array_agg(a.attname ORDER BY a.attname), ',') AS cols
         FROM pg_index x
         JOIN pg_class i ON i.oid = x.indexrelid
         JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = ANY (x.indkey)
         WHERE x.indrelid = ?::regclass AND x.indisunique AND NOT x.indisprimary
         GROUP BY i.relname",
        [$table],
    );

    return collect($rows)->mapWithKeys(fn (object $r): array => [$r->name => explode(',', $r->cols)])->all();
}

it('creates all eight agent tables', function (): void {
    foreach (AGENT_TABLES as $table) {
        expect(Schema::hasTable($table))->toBeTrue("{$table} missing");
        expect(Schema::hasColumns($table, ['id', 'created_at', 'updated_at']))->toBeTrue($table);
    }
});

it('has the unique indexes the runtime upserts on', function (): void {
    expect(array_values(uniqueIndexes('agent_usage_daily')))->toEqual([['agent', 'day', 'env']])
        ->and(array_values(uniqueIndexes('agent_states')))->toEqual([['agent', 'env']])
        ->and(array_values(uniqueIndexes('agent_scores')))->toEqual([['agent', 'day', 'env']])
        ->and(collect(uniqueIndexes('prompt_versions'))->values()->sort()->values()->all())
        ->toEqual([['agent', 'sha256'], ['agent', 'version']]);
});

it('makes every jsonb column NOT NULL with an empty-object default', function (): void {
    $cols = collect(DB::select(
        "SELECT table_name, column_name, is_nullable, column_default FROM information_schema.columns
         WHERE table_schema = current_schema() AND data_type = 'jsonb' AND table_name = ANY (?)",
        ['{'.implode(',', AGENT_TABLES).'}'],
    ));

    expect($cols->map(fn (object $c): string => "{$c->table_name}.{$c->column_name}")->sort()->values()->all())
        ->toEqual(['agent_notebook.data', 'agent_scores.calibration', 'agent_scores.data', 'journal_entries.data', 'research_reports.data']);
    foreach ($cols as $c) {
        expect($c->is_nullable)->toBe('NO')
            ->and($c->column_default)->toStartWith("'{}'");
    }
});

it('inserts rows without jsonb columns and gets {} back', function (): void {
    $id = (string) Str::uuid();
    DB::table('research_reports')->insert([
        'id' => $id, 'env' => 'demo', 'agent' => 'historian', 'kind' => 'regime_table', 'content_md' => 'x',
    ]);
    DB::table('journal_entries')->insert([
        'id' => (string) Str::uuid(), 'env' => 'demo', 'agent' => 'portfolio_manager', 'title' => 'BTC long',
    ]);

    expect(DB::table('research_reports')->where('id', $id)->value('data'))->toBe('{}')
        ->and(DB::table('journal_entries')->value('tags'))->toBe('{}');
});

it('rejects enum values outside the agreed lists', function (string $table, array $row): void {
    expect(fn () => DB::table($table)->insert(['id' => (string) Str::uuid(), ...$row]))
        ->toThrow(QueryException::class);
})->with([
    'run trigger' => ['agent_runs', ['env' => 'demo', 'agent' => 'historian', 'trigger' => 'cron', 'started_at' => now()]],
    'run status' => ['agent_runs', ['env' => 'demo', 'agent' => 'historian', 'trigger' => 'eval', 'status' => 'done', 'started_at' => now()]],
    'agent state' => ['agent_states', ['env' => 'demo', 'agent' => 'historian', 'state' => 'asleep']],
    'env' => ['agent_usage_daily', ['env' => 'paper', 'agent' => 'historian', 'day' => '2026-10-29']],
    'confidence > 1' => ['research_reports', ['env' => 'demo', 'agent' => 'historian', 'kind' => 'k', 'content_md' => 'x', 'confidence' => 1.5]],
]);

it('refuses a second prompt version with the same body hash', function (): void {
    $sha = hash('sha256', 'prompt body');
    DB::table('prompt_versions')->insert(['id' => (string) Str::uuid(), 'agent' => 'historian', 'version' => 1, 'sha256' => $sha, 'body' => 'prompt body']);

    expect(fn () => DB::table('prompt_versions')->insert(['id' => (string) Str::uuid(), 'agent' => 'historian', 'version' => 2, 'sha256' => $sha, 'body' => 'prompt body']))
        ->toThrow(UniqueConstraintViolationException::class);
});

it('accumulates agent_usage_daily with an upsert', function (): void {
    $row = fn (int $in, int $out): array => [
        'id' => (string) Str::uuid(), 'agent' => 'historian', 'env' => 'demo', 'day' => '2026-10-29',
        'tokens_in' => $in, 'tokens_out' => $out, 'turns' => 2, 'runs' => 1,
    ];
    $add = [
        'tokens_in' => DB::raw('agent_usage_daily.tokens_in + excluded.tokens_in'),
        'tokens_out' => DB::raw('agent_usage_daily.tokens_out + excluded.tokens_out'),
        'turns' => DB::raw('agent_usage_daily.turns + excluded.turns'),
        'runs' => DB::raw('agent_usage_daily.runs + excluded.runs'),
    ];

    DB::table('agent_usage_daily')->upsert([$row(100, 10)], ['agent', 'env', 'day'], $add);
    DB::table('agent_usage_daily')->upsert([$row(50, 5)], ['agent', 'env', 'day'], $add);

    $rows = DB::table('agent_usage_daily')->get();
    expect($rows)->toHaveCount(1)
        ->and((int) $rows[0]->tokens_in)->toBe(150)
        ->and((int) $rows[0]->tokens_out)->toBe(15)
        ->and((int) $rows[0]->turns)->toBe(4)
        ->and((int) $rows[0]->runs)->toBe(2);
});

it('rolls back cleanly and migrates again', function (): void {
    $migration = agentTablesMigration();

    $migration->down();
    foreach (AGENT_TABLES as $table) {
        expect(Schema::hasTable($table))->toBeFalse("{$table} still exists after down()");
    }

    $migration->up();
    foreach (AGENT_TABLES as $table) {
        expect(Schema::hasTable($table))->toBeTrue("{$table} missing after up()");
    }
});
