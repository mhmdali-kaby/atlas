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

/**
 * ATL-222 (QA round 1): the list fields validated element by element (`changes.<field>.*`),
 * per UpdateSettingsRequest::rules(). An empty string fails every one of them at "required",
 * the same failure QA's probe used to find the raw-key regression.
 *
 * @return list<string>
 */
function atl222ListFieldsWithElementRules(): array
{
    return [
        ...SettingsSchema::MARKET_LIST_FIELDS,
        ...SettingsSchema::INSTRUMENT_LIST_FIELDS,
        ...SettingsSchema::WEB_DOMAIN_FIELDS,
    ];
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

/**
 * ATL-222 (QA round 1): the per-entry `changes.<field>.*` rules on list fields were missing from
 * attributes(), so an empty whitelist/blocklist/enabled_markets/agents.web domain-list row showed
 * "The changes.whitelist.0 field is required." in both languages.
 */
it('never shows a raw settings key in a per-entry 422 message, for every list field', function (string $locale): void {
    $headers = $locale === 'ar' ? ['Accept-Language' => 'ar'] : [];

    foreach (atl222ListFieldsWithElementRules() as $field) {
        $response = $this->withHeaders($headers)->patchJson('/api/settings', [
            'env' => 'demo',
            'version' => 1,
            'changes' => [$field => ['']],
        ])->assertUnprocessable();

        $allErrors = $response->json('errors');
        expect($allErrors)->toBeArray();
        $messages = $allErrors["changes.{$field}.0"] ?? null;

        expect($messages)->not->toBeNull("expected a validation error on changes.{$field}.0")
            ->and($messages)->toBeArray()
            ->and($messages)->not->toBeEmpty();

        foreach ($messages as $message) {
            expect($message)->toBeString()
                ->and($message)->not->toContain($field, "field [{$field}]'s entry message names its own raw key: {$message}")
                ->and($message)->not->toContain('_', "field [{$field}]'s entry message has an underscore: {$message}");
        }
    }
})->with(['en' => ['en'], 'ar' => ['ar']]);

/**
 * ATL-222 (QA round 1): QA's exact probe — a 65-character whitelist entry (max:64) and a
 * duplicate enabled_markets entry (distinct) — reproduced directly, in both languages.
 */
it('names the field in plain words for QA\'s exact max-length and duplicate probes', function (string $locale): void {
    $headers = $locale === 'ar' ? ['Accept-Language' => 'ar'] : [];
    $expectedWhitelistLabel = $locale === 'ar' ? 'أداة مسموح بها' : 'Allowed instrument';
    $expectedMarketLabel = $locale === 'ar' ? 'فئة الأصل' : 'Asset class';

    $tooLong = $this->withHeaders($headers)->patchJson('/api/settings', [
        'env' => 'demo', 'version' => 1, 'changes' => ['whitelist' => [str_repeat('A', 65)]],
    ])->assertUnprocessable();
    $tooLongMessages = $tooLong->json('errors')['changes.whitelist.0'] ?? [];
    expect($tooLongMessages)->not->toBeEmpty();
    // One message is the max:64 rule (names the field via :attribute); the other is the
    // instId-format closure, which never named the field at all. Neither may leak the raw key.
    $namesTheField = array_filter($tooLongMessages, fn (string $message): bool => str_contains($message, $expectedWhitelistLabel));
    expect($namesTheField)->not->toBeEmpty("expected one of {$tooLong->getContent()} to name [{$expectedWhitelistLabel}]");
    foreach ($tooLongMessages as $message) {
        expect($message)->not->toContain('whitelist.0')
            ->and($message)->not->toContain('_');
    }

    $duplicate = $this->withHeaders($headers)->patchJson('/api/settings', [
        'env' => 'demo', 'version' => 1, 'changes' => ['enabled_markets' => ['crypto_perp', 'crypto_perp']],
    ])->assertUnprocessable();
    $duplicateMessages = $duplicate->json('errors')['changes.enabled_markets.0'] ?? [];
    expect($duplicateMessages)->not->toBeEmpty();
    foreach ($duplicateMessages as $message) {
        expect($message)->toContain($expectedMarketLabel)
            ->and($message)->not->toContain('enabled_markets.0')
            ->and($message)->not->toContain('_');
    }
})->with(['en' => ['en'], 'ar' => ['ar']]);
