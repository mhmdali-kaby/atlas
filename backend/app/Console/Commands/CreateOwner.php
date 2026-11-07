<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Models\User;
use Illuminate\Console\Attributes\Description;
use Illuminate\Console\Attributes\Signature;
use Illuminate\Console\Command;
use Illuminate\Support\Facades\Validator;
use Illuminate\Validation\Rules\Password;

/**
 * Creates the single Atlas owner interactively. There are no default or seeded credentials.
 */
#[Signature('atlas:create-owner')]
#[Description('Create the Atlas owner account (refuses if an owner already exists)')]
class CreateOwner extends Command
{
    public function handle(): int
    {
        if (User::query()->exists()) {
            $this->components->error('An owner already exists. Atlas has a single owner; nothing was created.');

            return self::FAILURE;
        }

        $name = trim((string) $this->ask('Owner name'));
        $email = strtolower(trim((string) $this->ask('Owner email')));
        $password = (string) $this->secret('Password (min 12 characters)');
        $confirmation = (string) $this->secret('Confirm password');

        $validator = Validator::make(
            ['name' => $name, 'email' => $email, 'password' => $password, 'password_confirmation' => $confirmation],
            [
                'name' => ['required', 'string', 'max:255'],
                'email' => ['required', 'string', 'email', 'max:255'],
                'password' => ['required', 'string', 'confirmed', Password::min(12)],
            ],
        );

        if ($validator->fails()) {
            foreach ($validator->errors()->all() as $message) {
                $this->components->error($message);
            }

            return self::FAILURE;
        }

        // The `hashed` cast on User stores the password with bcrypt.
        User::query()->create([
            'name' => $name,
            'email' => $email,
            'password' => $password,
        ]);

        $this->components->info("Owner {$email} created.");

        return self::SUCCESS;
    }
}
