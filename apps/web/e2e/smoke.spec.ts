import { expect, test } from "@playwright/test";

const PAGES = [
  ["/", /The chain never sleeps/],
  ["/mint", /Mint AGENT\/\/98 access/],
  ["/terminal", /Connect and verify a wallet/],
  ["/marketplace", /Agent marketplace/],
  ["/scanner", /Token scanner/],
  ["/wallet", /Wallet intelligence/],
  ["/portfolio", /Unified portfolio/],
  ["/simulator", /Transaction simulator/],
  ["/automations", /Automations/],
  ["/activity", /Activity & execution history/],
  ["/settings", /Settings/],
  ["/docs", /How AGENT\/\/98 works/],
  ["/legal/terms", /Terms of use/],
  ["/legal/privacy", /Privacy/],
  ["/legal/risk", /Risk disclosures/],
] as const;

for (const [path, heading] of PAGES) {
  test(`renders ${path}`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(path);
    await expect(page.locator("main").getByText(heading).first()).toBeVisible();
    expect(errors, errors.join("\n")).toEqual([]);
  });
}

test("mint checkout computes the exact total and bounds quantity", async ({ page }) => {
  await page.goto("/mint");
  await expect(page.getByTestId("mint-total")).toContainText("1.98");
  await page.getByLabel("Increase quantity").click();
  await page.getByLabel("Increase quantity").click();
  await expect(page.getByTestId("mint-total")).toContainText("5.94");
  await page.getByLabel("Quantity", { exact: true }).fill("999");
  await expect(page.getByLabel("Quantity", { exact: true })).toHaveValue("50");
  await expect(page.getByTestId("mint-total")).toContainText("99.00");
});

test("chain toggle switches the checkout to Solana", async ({ page }) => {
  await page.goto("/mint");
  await page.getByRole("radio", { name: /solana/i }).first().click();
  await expect(page.getByRole("button", { name: /Connect Solana wallet|Connect/ }).first()).toBeVisible();
});

test("supply degrades gracefully when the API is unreachable", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/API offline|not deployed|\d/).first()).toBeVisible();
});
