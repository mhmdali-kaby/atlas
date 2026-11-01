import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import arWarroom from "../src/i18n/locales/ar/warroom.json" with { type: "json" };
import {
    BREAKEVEN_PROPOSAL_ID,
    dailyReportBreachMessage,
    positionUpdateMessage,
} from "../src/features/warroom/fixtures/pm";

/**
 * ATL-178: the Portfolio Manager's daily_report and position_update cards in the War Room feed
 * (brief §7.8, design v14). A fake Laravel serves the Main thread with a daily report that has one
 * breach and a position update whose breakeven rule links to its proposal.
 */
const SHOTS = "../../evidence/ATL-178";
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

/** The agent's own words in the owner's language, so the Arabic screenshots carry no English prose. */
function messages(lang: "en" | "ar") {
    const daily = dailyReportBreachMessage();
    const pu = positionUpdateMessage();
    if (lang === "ar") {
        daily.content_md =
            "**التقرير اليومي، الثلاثاء 27 أكتوبر.** الرصيد +4,026.06$ (+0.08%) من ثلاث صفقات مغلقة. تم تجاوز حدّ واحد.";
        const card = daily.card as Record<string, Record<string, unknown>>;
        card.best_trade!.note = "بلغ الهدف 2 بعد خبر تدفّقات الصناديق";
        card.worst_trade!.note = "ضُرب الوقف عند افتتاح السوق الأمريكية";
        (card.tomorrow!.events as Record<string, unknown>[])[0]!.name =
            "الناتج المحلي الأمريكي، القراءة الأولى";
        pu.content_md =
            "**مركز البيع في XAU-USDT-SWAP بلغ +1.0R.** فُعِّلت قاعدة نقطة التعادل، فاقترحتُ نقل الوقف إلى 4,117.6.";
    }
    // The API's order: newest first.
    return [pu, daily];
}

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

async function open(
    page: Page,
    lang: "en" | "ar",
    theme: "light" | "dark",
    width = 1280,
) {
    await page.setViewportSize({ width, height: 1800 });
    await page.clock.install({ time: new Date("2026-10-28T11:32:00Z") });
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
    const daily = page.getByTestId("dr-card");
    const pu = page.getByTestId("pu-card");
    await expect(daily).toHaveCount(1);
    await expect(pu).toHaveCount(1);
    return { daily, pu };
}

async function axeViolations(page: Page) {
    const results = await new AxeBuilder({ page })
        .include('[data-testid="dr-card"]')
        .include('[data-testid="pu-card"]')
        .analyze();
    return results.violations.flatMap((v) =>
        v.nodes.map(
            (n) => `${v.id} ${n.target.join(" ")}: ${n.failureSummary ?? ""}`,
        ),
    );
}

test("Arabic RTL, dark: both cards render in Arabic with no axe violations", async ({
    page,
}) => {
    const { daily, pu } = await open(page, "ar", "dark");
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.locator("html")).toHaveClass(/dark/);

    await expect(daily.getByTestId("dr-trade")).toHaveCount(3);
    await expect(daily.getByTestId("dr-best")).toContainText(arWarroom.pm.best);
    await expect(daily.getByTestId("dr-worst")).toContainText(
        arWarroom.pm.worst,
    );
    const breach = daily.getByRole("alert");
    await expect(breach).toContainText(arWarroom.pm.breachTitle_one);
    // The table's first column is on the right in RTL.
    const head = daily.locator(".an-table thead th");
    expect((await head.nth(0).boundingBox())!.x).toBeGreaterThan(
        (await head.nth(1).boundingBox())!.x,
    );

    await expect(pu.getByTestId("pu-rule-name")).toHaveText(
        "القاعدة: نقطة التعادل",
    );
    await expect(pu.getByTestId("pu-proposal-link")).toHaveAttribute(
        "href",
        `/warroom?proposal=${BREAKEVEN_PROPOSAL_ID}`,
    );
    await expect(pu.getByTestId("ladder-compact")).toHaveAttribute(
        "dir",
        "ltr",
    );

    await page.waitForTimeout(300);
    expect(await axeViolations(page)).toEqual([]);
    await daily.screenshot({ path: `${SHOTS}/daily-ar-dark.png` });
    await pu.screenshot({ path: `${SHOTS}/pu-ar-dark.png` });
});

for (const [lang, theme] of [
    ["en", "light"],
    ["en", "dark"],
    ["ar", "light"],
] as const) {
    test(`${lang} ${theme}: no axe violations`, async ({ page }) => {
        const { daily, pu } = await open(page, lang, theme);
        await page.waitForTimeout(300);
        expect(await axeViolations(page)).toEqual([]);
        await daily.screenshot({ path: `${SHOTS}/daily-${lang}-${theme}.png` });
        await pu.screenshot({ path: `${SHOTS}/pu-${lang}-${theme}.png` });
    });
}

test("narrow feed: the trades table stacks and nothing scrolls sideways", async ({
    page,
}) => {
    const { daily, pu } = await open(page, "ar", "light", 390);
    await expect(daily.locator(".an-table thead")).toBeHidden();
    const overflow = await page.evaluate(
        () =>
            document.documentElement.scrollWidth -
            document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    await daily.screenshot({ path: `${SHOTS}/daily-ar-390.png` });
    await pu.screenshot({ path: `${SHOTS}/pu-ar-390.png` });
});
