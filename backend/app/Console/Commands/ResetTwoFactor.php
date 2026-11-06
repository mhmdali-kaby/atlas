<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Models\User;
use App\Support\Auditable;
use App\Support\TwoFactor\TwoFactor;
use Illuminate\Console\Attributes\Description;
use Illuminate\Console\Attributes\Signature;
use Illuminate\Console\Command;
use Illuminate\Support\Facades\DB;

/**
 * ATL-255, decision #265 (1): the login design and the Settings → Security page point an owner who
 * lost both their phone and every recovery code at `./atlas reset-two-factor` in Terminal, because
 * that is the only way back in once both factors are gone. Turns 2FA off through TwoFactor::disable()
 * (never writes the columns directly) and records one audit_log row. Never prints a secret or a code.
 */
#[Signature('atlas:reset-two-factor {--email= : Owner email (optional with a single owner)} {--force : Skip the confirmation prompt}')]
#[Description('Turn off two-factor sign-in for an owner who lost both their phone and their recovery codes')]
class ResetTwoFactor extends Command
{
    public function handle(TwoFactor $twoFactor, Auditable $audit): int
    {
        $user = $this->resolveOwner();
        if ($user === null) {
            return self::FAILURE;
        }

        if (! $twoFactor->enabled($user)) {
            $this->components->info(__('two_factor.cli.already_off', ['email' => $user->email]));

            return self::SUCCESS;
        }

        if ($this->option('force') !== true && ! $this->confirm(__('two_factor.cli.confirm', ['email' => $user->email]))) {
            $this->components->warn(__('two_factor.cli.cancelled'));

            return self::FAILURE;
        }

        DB::transaction(function () use ($user, $twoFactor, $audit): void {
            $twoFactor->disable($user);
            $audit->record(
                'two_factor.reset',
                'users',
                $user->id,
                ['enabled' => true],
                ['enabled' => false, 'actor' => 'cli'],
                $user,
            );
        });

        $this->components->info(__('two_factor.cli.done', ['email' => $user->email]));

        return self::SUCCESS;
    }

    private function resolveOwner(): ?User
    {
        $email = $this->option('email');
        if (is_string($email) && trim($email) !== '') {
            $user = User::query()->where('email', strtolower(trim($email)))->first();
            if ($user === null) {
                $this->components->error(__('two_factor.cli.no_such_owner', ['email' => $email]));

                return null;
            }

            return $user;
        }

        $count = User::query()->count();
        if ($count === 0) {
            $this->components->error(__('two_factor.cli.no_owner'));

            return null;
        }
        if ($count > 1) {
            $this->components->error(__('two_factor.cli.multiple_owners'));

            return null;
        }

        /** @var User */
        return User::query()->first();
    }
}
