import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import arJournal from "../src/i18n/locales/ar/journal.json" with { type: "json" };
import arWarroom from "../src/i18n/locales/ar/warroom.json" with { type: "json" };
import enJournal from "../src/i18n/locales/en/journal.json" with { type: "json" };
import enWarroom from "../src/i18n/locales/en/warroom.json" with { type: "json" };
import {
    JOURNAL_ID,
    postMortemMessage,
    quietWeekMessage,
    weeklyReviewMessage,
} from "../src/features/warroom/fixtures/learning";

/**
 * ATL-203: the Portfolio Manager's post_mortem card and the Historian's weekly_review card in the
 * War Room feed (brief §7.3, §7.8, design v16 §9). A fake Laravel serves the Main thread with a
 * quiet week, a weekly review and a post-mortem.
 */
const SHOTS = "../../evidence/ATL-203";
const OWNER = {
    id: "01a18376-1d35-70c2-ad01-6d1d2cd870ad",
    name: "Mohammed Ali",
    email: "owner@atlas.test",
    locale: "en",
    theme: "system",
};
const MAIN = {
    id: "thread-main",
    kind: "main",
    key: "main",
    title: "Main",
    last_message_at: "2026-10-28T10:45:00.000000Z",
    created_at: "2026-10-01T00:00:00.000000Z",
};

async function fakeApi(page: Page, lang: "en" | "ar") {
    await page.route(/^http:\/\/localhost:8000\//, async (route) => {
        const req = route.request();
        const url = new URL(req.url());
        const origin = req.headers()["origin"] ?? "*";
        const json = (status: number, body?: unknown) =>
            route.fulfill({
                status,
                contentType: "application/json",
                headers: {
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Credentials": "true",
                },
                body: body === undefined ? "" : JSON.stringify(body),
            });
        if (req.method() === "OPTIONS") {
            return route.fulfill({
                status: 204,
                headers: {
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Credentials": "true",
                    "Access-Control-Allow-Headers": "*",
                    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE",
                },
            });
        }
        const p = url.pathname;
        if (p === "/sanctum/csrf-cookie") return json(204);
        if (p === "/api/auth/status") return json(200, { owner_exists: true });
        if (p === "/api/auth/me") return json(200, { data: OWNER });
        if (p === "/api/warroom/threads") return json(200, { data: [MAIN] });
        if (p === "/api/warroom/messages") {
            const data =
                url.searchParams.get("thread_id") === MAIN.id
                    ? messages(lang)
                    : [];
            return json(200, { data, meta: { next_cursor: null } });
        }
        if (p === "/api/portfolio/positions") return json(200, { data: [] });
        return json(404, { message: "Not found" });
    });
}


/** The agents' own words in the owner's language, so the Arabic screenshots carry no English prose. */
function messages(lang: "en" | "ar") {
    const quiet = quietWeekMessage();
    const week = weeklyReviewMessage();
    const pm = postMortemMessage();
    if (lang === "ar") {
        quiet.content_md = "لا صفقات مغلقة هذا الأسبوع.";
        week.content_md =
            "**المراجعة الأسبوعية.** تغيير واحد للنظر فيه أدناه. يدعم التاريخ بقية إعدادات الأسبوع.";
        const wc = week.card as Record<string, unknown>;
        wc.verdict = "يدعم التاريخ العملية دعمًا ضعيفًا: حققت إعدادات الاختراق 0.50R في صفقتين.";
        wc.suggestion = { change: "ارفع الحد الأدنى لـ R في إعدادات الاختراق إلى 2.0", reason: "n = 2", where: "risk" };
        pm.content_md = "**SPY: −0.97R.** الدرس: قبل افتتاح السوق الأمريكية، امنح وقف عقود الأسهم 1.2 ATR(1H) على الأقل.";
        (pm.card as Record<string, unknown>).lesson =
            "قبل افتتاح السوق الأمريكية، امنح وقف عقود الأسهم 1.2 ATR(1H) على الأقل؛ فالافتتاح يقفز فوق الأوقاف الأضيق.";
    }
    // The API's order: newest first.
    return [pm, week, quiet];
}

async function open(page: Page, lang: "en" | "ar", theme: "light" | "dark", width = 1280) {
    await page.setViewportSize({ width, height: 2400 });
    await page.clock.install({ time: new Date("2026-10-27T14:20:00Z") });
    await page.addInitScript(
        ([l, th]) => {
            localStorage.setItem("atlas.lang", l);
            localStorage.setItem("atlas.theme", th);
        },
        [lang, theme] as const,
    );
    await page.emulateMedia({ colorScheme: theme });
    await fakeApi(page, lang);
    await page.goto("/warroom");
    const pm = page.getByTestId("pmo-card");
    const weeks = page.getByTestId("wk-card");
    await expect(pm).toHaveCount(1);
    await expect(weeks).toHaveCount(2);
    return { pm, week: weeks.filter({ has: page.getByTestId("wk-table") }), quiet: weeks.filter({ has: page.getByTestId("wk-quiet") }) };
}

async function axeViolations(page: Page) {
    const results = await new AxeBuilder({ page })
        .include('[data-testid="pmo-card"]')
        .include('[data-testid="wk-card"]')
        .analyze();
    return results.violations.flatMap((v) =>
        v.nodes.map((n) => `${v.id} ${n.target.join(" ")}: ${n.failureSummary ?? ""}`),
    );
}

test("English light: both cards, the journal link and no axe violations", async ({ page }) => {
    const { pm, week, quiet } = await open(page, "en", "light");
    await expect(pm.getByTestId("pmo-outcome")).toContainText(enWarroom.pmo.out.loss);
    await expect(pm.getByTestId("pmo-close")).toHaveText(enJournal.rule.stop);
    await expect(pm.getByTestId("pmo-r")).toHaveText("−0.97R");
    await expect(pm.getByTestId("pmo-usd")).toContainText("−$20.55");
    await expect(pm.getByTestId("ld-exit")).toBeVisible();
    await expect(pm.getByTestId("pmo-journal")).toHaveAttribute("href", `/journal?entry=${JOURNAL_ID}`);
    await expect(week.getByTestId("wk-row")).toHaveCount(2);
    await expect(week.getByTestId("wk-change")).toHaveCount(1);
    await expect(quiet.getByTestId("wk-quiet")).toContainText(enWarroom.wk.none);

    await week.getByRole("radio", { name: enWarroom.wk.byClass }).click();
    await expect(week.getByTestId("wk-row")).toHaveCount(3);
    await week.getByRole("radio", { name: enWarroom.wk.bySetup }).click();

    await page.waitForTimeout(300);
    expect(await axeViolations(page)).toEqual([]);
    await pm.screenshot({ path: `${SHOTS}/pm-en-light.png` });
    await week.screenshot({ path: `${SHOTS}/week-en-light.png` });
    await quiet.screenshot({ path: `${SHOTS}/quiet-en-light.png` });
});

test("Arabic RTL, dark: both cards in Arabic with no axe violations", async ({ page }) => {
    const { pm, week, quiet } = await open(page, "ar", "dark");
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(pm.getByTestId("pmo-close")).toHaveText(arJournal.rule.stop);
    await expect(pm.getByTestId("pmo-outcome")).toContainText(arWarroom.pmo.out.loss);
    await expect(pm.getByTestId("ladder-compact")).toHaveAttribute("dir", "ltr");
    await expect(pm.getByTestId("pmo-journal")).toContainText(arWarroom.pmo.open);
    await expect(week.getByTestId("wk-change-link")).toHaveAttribute("href", "/settings/risk");
    // The table's first column is on the right in RTL.
    const head = week.locator(".wk-table thead th");
    expect((await head.nth(0).boundingBox())!.x).toBeGreaterThan((await head.nth(1).boundingBox())!.x);
    await expect(quiet.getByTestId("wk-quiet")).toContainText(arWarroom.wk.none);

    await page.waitForTimeout(300);
    expect(await axeViolations(page)).toEqual([]);
    await pm.screenshot({ path: `${SHOTS}/pm-ar-dark.png` });
    await week.screenshot({ path: `${SHOTS}/week-ar-dark.png` });
    await quiet.screenshot({ path: `${SHOTS}/quiet-ar-dark.png` });
});

for (const [lang, theme] of [
    ["en", "dark"],
    ["ar", "light"],
] as const) {
    test(`${lang} ${theme}: no axe violations`, async ({ page }) => {
        const { pm, week } = await open(page, lang, theme);
        await page.waitForTimeout(300);
        expect(await axeViolations(page)).toEqual([]);
        await pm.screenshot({ path: `${SHOTS}/pm-${lang}-${theme}.png` });
        await week.screenshot({ path: `${SHOTS}/week-${lang}-${theme}.png` });
    });
}

test("narrow feed: nothing scrolls the page sideways", async ({ page }) => {
    const { pm, week } = await open(page, "ar", "light", 390);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await pm.screenshot({ path: `${SHOTS}/pm-ar-390.png` });
    await week.screenshot({ path: `${SHOTS}/week-ar-390.png` });
});
