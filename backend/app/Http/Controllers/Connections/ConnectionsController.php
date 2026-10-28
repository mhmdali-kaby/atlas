<?php

declare(strict_types=1);

namespace App\Http\Controllers\Connections;

use App\Http\Controllers\Controller;
use App\Http\Requests\Connections\StoreConnectionRequest;
use App\Http\Requests\Connections\UpdateConnectionRequest;
use App\Http\Resources\ConnectionResource;
use App\Models\Connection;
use App\Support\Auditable;
use Illuminate\Database\UniqueConstraintViolationException;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Resources\Json\AnonymousResourceCollection;
use Illuminate\Http\Response;
use Illuminate\Support\Facades\DB;
use Illuminate\Validation\ValidationException;

/**
 * Exchange API connections (brief §14.2, §16). Secrets go in encrypted and only come back masked;
 * every write is audited with secrets replaced by api_key_last4 and the names of changed secrets.
 */
class ConnectionsController extends Controller
{
    public function __construct(private readonly Auditable $audit) {}

    public function index(): AnonymousResourceCollection
    {
        return ConnectionResource::collection(
            Connection::query()->orderBy('exchange')->orderBy('env')->get(),
        );
    }

    public function store(StoreConnectionRequest $request): JsonResponse
    {
        /** @var array{env: string, label: string, api_key: string, api_secret: string, passphrase: string} $data */
        $data = $request->safe()->only(['env', 'label', 'api_key', 'api_secret', 'passphrase']);

        try {
            $connection = DB::transaction(function () use ($data, $request): Connection {
                $connection = Connection::query()->create([...$data, 'exchange' => $request->exchange()]);
                $this->audit->record(
                    action: 'connection.created',
                    subjectType: 'connections',
                    subjectId: $connection->id,
                    before: null,
                    after: [...$connection->auditSnapshot(), 'secrets_changed' => Connection::SECRET_FIELDS],
                );

                return $connection;
            });
        } catch (UniqueConstraintViolationException) {
            // Lost a race with a concurrent create for the same exchange + env.
            throw ValidationException::withMessages(['env' => __('connections.already_exists')]);
        }

        return (new ConnectionResource($connection))->response()->setStatusCode(201);
    }

    public function update(UpdateConnectionRequest $request, Connection $connection): ConnectionResource
    {
        /** @var array<string, string> $changes */
        $changes = $request->safe()->only(['label', ...Connection::SECRET_FIELDS]);

        DB::transaction(function () use ($connection, $changes): void {
            $before = $connection->auditSnapshot();

            $connection->fill($changes);
            $secretsChanged = array_values(array_filter(
                Connection::SECRET_FIELDS,
                fn (string $field): bool => $connection->isDirty($field),
            ));
            if ($secretsChanged !== []) {
                // Permissions, modes and UID belonged to the old key; Test connection must run again.
                $connection->fill(array_fill_keys(Connection::VERIFICATION_FIELDS, null));
            }

            if (! $connection->isDirty()) {
                return;
            }
            $connection->save();

            $this->audit->record(
                action: 'connection.updated',
                subjectType: 'connections',
                subjectId: $connection->id,
                before: $before,
                after: [...$connection->auditSnapshot(), 'secrets_changed' => $secretsChanged],
            );
        });

        return new ConnectionResource($connection->refresh());
    }

    public function destroy(Connection $connection): Response
    {
        DB::transaction(function () use ($connection): void {
            $before = $connection->auditSnapshot();
            $connection->delete();
            $this->audit->record(
                action: 'connection.deleted',
                subjectType: 'connections',
                subjectId: $connection->id,
                before: $before,
                after: null,
            );
        });

        return response()->noContent();
    }
}
