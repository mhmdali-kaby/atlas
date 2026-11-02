<?php

declare(strict_types=1);

use App\Models\User;
use App\Support\Settings\SettingsSchema;

/**
 * ATL-222 (security round 16 / QA on ATL-216): every settings key gets a human attribute name,
 * not just autonomy_ask_above_usd, so a 422 never shows the owner a raw underscored key.
 */

/** A value that fails that field's own validation rule, without ever matching another field. */
function atl222BadValueFor(string $field): mixed
{
    if (in_array($field, SettingsSchema::WEB_DOMAIN_FIELDS, true)) {
        return 'not-a-list';
    }

    $agentFields = SettingsSchema::agentFields();
    if (isset($agentFields[$field])) {
        $setting = substr($field, (int) strrpos($field, '.') + 1);

        return $setting === 'enabled' ? 'not-a-bool' : true;
    }

    if (in_array($field, SettingsSchema::notificationFields(), true)) {
        return 'not-a-bool';
    }

    if ($field === 'risk_profile' || isset(SettingsSchema::NUMERIC_FIELDS[$field]) || isset(SettingsSchema::CHOICE_FIELDS[$field])) {
        return true;
    }

    if (in_array($field, SettingsSchema::BOOLEAN_FIELDS, true)) {
        return 'not-a-bool';
    }

    if (in_array($field, SettingsSchema::MARKET_LIST_FIELDS, true)
        || in_array($field, SettingsSchema::INSTRUMENT_LIST_FIELDS, true)
        || $field === SettingsSchema::BLOCKLIST_UNTIL_FIELD) {
        return 'not-a-list';
    }

    throw new RuntimeException("atl222BadValueFor: no case for settings field [{$field}].");
}

beforeEach(function (): void {
    $this->actingAs(User::factory()->create(), 'web');
    $this->getJson('/api/settings?env=demo')->assertOk();
});

it('never shows a raw settings key in a 422 message, for every settings field', function (): void {
    foreach (SettingsSchema::fields() as $field) {
        $response = $this->patchJson('/api/settings', [
            'env' => 'demo',
            'version' => 1,
            'changes' => [$field => atl222BadValueFor($field)],
        ])->assertUnprocessable();

        $allErrors = $response->json('errors');
        expect($allErrors)->toBeArray();
        $messages = $allErrors['changes.'.$field] ?? null;

        expect($messages)->not->toBeNull("expected a validation error on changes.{$field}")
            ->and($messages)->toBeArray()
            ->and($messages)->not->toBeEmpty();

        foreach ($messages as $message) {
            expect($message)->toBeString()
                ->and($message)->not->toContain($field, "field [{$field}]'s message names its own raw key: {$message}")
                ->and($message)->not->toContain('_', "field [{$field}]'s message has an underscore: {$message}");
        }
    }
});
