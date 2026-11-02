<?php

declare(strict_types=1);

namespace App\Models;

use Database\Factories\UserFactory;
use Illuminate\Database\Eloquent\Attributes\Fillable;
use Illuminate\Database\Eloquent\Attributes\Hidden;
use Illuminate\Database\Eloquent\Concerns\HasUuids;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Foundation\Auth\User as Authenticatable;
use Illuminate\Notifications\Notifiable;

/**
 * The single Atlas owner (the table stays multi-user-ready).
 *
 * @property string $id UUID
 * @property string $name
 * @property string $email
 * @property string $locale en|ar
 * @property string $theme system|light|dark
 * @property string $timezone IANA name, display-only (ATL-226)
 */
#[Fillable(['name', 'email', 'password', 'locale', 'timezone', 'theme'])]
#[Hidden(['password', 'remember_token'])]
class User extends Authenticatable
{
    /** @use HasFactory<UserFactory> */
    use HasFactory, HasUuids, Notifiable;

    public const LOCALES = ['en', 'ar'];

    public const THEMES = ['system', 'light', 'dark'];

    public const DEFAULT_TIMEZONE = 'Asia/Baghdad';

    /** @var array<string, mixed> */
    protected $attributes = [
        'locale' => 'en',
        'timezone' => 'Asia/Baghdad',
        'theme' => 'system',
    ];

    /**
     * @return array<string, string>
     */
    protected function casts(): array
    {
        return [
            'email_verified_at' => 'datetime',
            'password' => 'hashed',
        ];
    }
}
