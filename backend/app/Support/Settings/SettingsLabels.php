<?php

declare(strict_types=1);

namespace App\Support\Settings;

/**
 * ATL-222: a human attribute name for every settings field, in the owner's language, so a 422
 * never shows a raw key such as "autonomy_ask_above_usd" (security round 16 / QA on ATL-216).
 * Plain fields come straight from lang/{en,ar}/settings.php's `attr` map; the dotted
 * `agents.<slug>.<setting>` and `notifications.<event>.<channel>` fields are composed from their
 * two short parts, so nine agents and eleven events do not need 69 separate translations.
 */
final class SettingsLabels
{
    public static function forField(string $field): string
    {
        if (in_array($field, SettingsSchema::WEB_DOMAIN_FIELDS, true)) {
            return self::flat($field);
        }

        if (isset(SettingsSchema::agentFields()[$field])) {
            [, $slug, $setting] = explode('.', $field);

            return __('settings.attr_agent_setting', [
                'agent' => __("settings.agents.{$slug}"),
                'setting' => __("settings.agent_setting.{$setting}"),
            ]);
        }

        if (in_array($field, SettingsSchema::notificationFields(), true)) {
            [, $event, $channel] = explode('.', $field);

            return __('settings.attr_notification', [
                'event' => __("settings.notification_event.{$event}"),
                'channel' => __("settings.notification_channel.{$channel}"),
            ]);
        }

        return self::flat($field);
    }

    /**
     * ATL-222 (QA round 1): the singular label for one entry of a list field — whitelist,
     * blocklist, enabled_markets and agents.web.allow_domains/deny_domains all validate each
     * element with its own `changes.<field>.*` rule (required, max length, distinct, ...), and
     * without an attribute for that wildcard key Laravel names the error after the raw path
     * ("changes.whitelist.0"). Falls back to the field's own (plural) label if no singular
     * translation exists, so a new list field never regresses to a raw key either.
     */
    public static function forListElement(string $field): string
    {
        $key = "settings.attr_element.{$field}";
        $label = __($key);

        return $label === $key ? self::forField($field) : $label;
    }

    /** The `attr.<field>` translation, or the field with underscores turned to spaces if it is missing. */
    private static function flat(string $field): string
    {
        $key = "settings.attr.{$field}";
        $label = __($key);

        return $label === $key ? str_replace('_', ' ', $field) : $label;
    }
}
