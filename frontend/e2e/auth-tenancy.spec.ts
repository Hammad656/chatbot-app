import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import path from "node:path";
import type { AuthSession } from "../app/resolve/types";

const ui = (page: Page) => page.getByRole("main");

const password = "test-only-ResolveAI-password-1248";
const unique = () => randomUUID().replaceAll("-", "").slice(0, 12);

async function signup(page: Page, email: string, name: string) {
  await page.goto("/signup");
  await ui(page).getByLabel("Full name", { exact: true }).fill(name);
  await ui(page).getByLabel("Email address", { exact: true }).fill(email);
  await ui(page).getByLabel("Password", { exact: true }).fill(password);
  const response = page.waitForResponse(
    (result) =>
      result.url().endsWith("/api/v1/auth/signup") &&
      result.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  expect((await response).status()).toBe(201);
  await expect(page).toHaveURL(/\/onboarding$/);
}

async function createWorkspace(
  page: Page,
  name: string,
  first = false,
): Promise<AuthSession> {
  await page
    .getByRole("button", {
      name: first ? "Create workspace" : "New workspace",
      exact: true,
    })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Workspace name", { exact: true }).fill(name);
  const switched = page.waitForResponse(
    (result) =>
      /\/api\/v1\/workspaces\/[^/]+\/switch$/.test(result.url()) &&
      result.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "Create", exact: true }).click();
  const response = await switched;
  expect(response.status()).toBe(200);
  await expect(page).toHaveURL(/\/knowledge$/);
  await expect(
    page.getByRole("heading", { name: "Company knowledge chat", exact: true }),
  ).toBeVisible();
  return (await response.json()) as AuthSession;
}

test("visible auth pages, login errors, reload, cross-tab refresh and logout", async ({
  page,
  context,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByRole("link", { name: "Create an account", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("login-desktop.png"),
    fullPage: true,
  });
  const email = `auth-${unique()}@example.com`;
  await signup(page, email, "Auth Test Owner");
  const previousWorkspace = "Auth browser workspace " + unique();
  await createWorkspace(page, previousWorkspace, true);
  const cookies = await context.cookies();
  const refresh = cookies.find((cookie) => cookie.name === "resolveai_refresh");
  expect(refresh?.httpOnly).toBe(true);
  expect(refresh?.sameSite).toBe("Lax");
  expect(cookies.some((cookie) => cookie.name === "morrow_session")).toBe(
    false,
  );
  const stored = await page.evaluate(() =>
    [...Object.values(localStorage), ...Object.values(sessionStorage)].join(
      " ",
    ),
  );
  expect(stored).not.toContain(refresh!.value);
  expect(stored).not.toMatch(/eyJ[A-Za-z0-9_-]+\./);
  expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401);
  const second = await context.newPage();
  await Promise.all([page.reload(), second.goto("/knowledge")]);
  for (const tab of [page, second])
    await expect(
      tab.getByRole("heading", { name: "Company knowledge chat", exact: true }),
    ).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(second).toHaveURL(/\/login$/);
  await second.close();
  await ui(page).getByLabel("Email address", { exact: true }).fill(email);
  await ui(page).getByLabel("Password", { exact: true }).fill("incorrect");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(ui(page).getByRole("alert")).toContainText(
    "Email or password is incorrect",
  );
  await ui(page).getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/knowledge$/);
  await expect(
    ui(page).getByText("Auth Test Owner", { exact: true }),
  ).toBeVisible();
  const schema = await (await page.request.get("/api/v1/openapi.json")).json();
  for (const endpoint of ["signup", "login", "refresh", "logout", "me"])
    expect(schema.paths[`/api/v1/auth/${endpoint}`]).toBeDefined();
  expect((await page.request.get("/docs")).status()).toBe(200);
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page
    .getByRole("link", { name: "Create an account", exact: true })
    .click();
  await expect(
    ui(page).getByLabel("Email address", { exact: true }),
  ).toHaveValue("");
  await expect(ui(page).getByLabel("Password", { exact: true })).toHaveValue(
    "",
  );
  await ui(page)
    .getByLabel("Full name", { exact: true })
    .fill("Different Account");
  await ui(page)
    .getByLabel("Email address", { exact: true })
    .fill(`other-${unique()}@example.com`);
  await ui(page).getByLabel("Password", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(page).toHaveURL(/\/onboarding$/);
  await expect(
    ui(page).getByRole("button", { name: "Create workspace", exact: true }),
  ).toBeVisible();
  await expect(ui(page)).not.toContainText(previousWorkspace);
  await expect(ui(page)).not.toContainText(email);
  expect(errors).toEqual([]);
});

test("company RAG stays isolated across workspaces, accounts and invitation roles", async ({
  page,
  browser,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const suffix = unique();
  const aName = "Atlas " + suffix;
  const bName = "Birch " + suffix;
  await signup(page, `owner-${suffix}@example.com`, "Company Owner");
  const atlas = await createWorkspace(page, aName, true);
  const companyId = atlas.user.active_workspace_id!;
  const uploaded = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/workspaces/${companyId}/documents`) &&
      response.request().method() === "POST",
  );
  const filename = "Northstar_Workspace_Company_Knowledge_Base.pdf";
  await ui(page)
    .locator('input[type="file"]')
    .setInputFiles(path.resolve(__dirname, "../../docs/demo", filename));
  const uploadedDocument = await (await uploaded).json();
  await expect(
    ui(page).getByText("ready", { exact: true }).first(),
  ).toBeVisible({
    timeout: 120000,
  });
  await ui(page)
    .getByLabel("Your question", { exact: true })
    .fill("What is the policy for order changes and cancellations?");
  await page
    .getByRole("button", { name: "Ask with sources", exact: true })
    .click();
  await expect(
    ui(page).locator(".r-answers article > p").first(),
  ).toContainText(/picking/i);
  expect(
    await ui(page).locator(".r-answers article details").count(),
  ).toBeGreaterThan(0);
  await page.reload();
  await expect(
    ui(page).locator(".r-answers article > p").first(),
  ).toContainText(/picking/i);
  await page.getByRole("button", { name: "New ticket", exact: true }).click();
  const ticketDialog = page.getByRole("dialog");
  await ticketDialog
    .getByLabel("Subject", { exact: true })
    .fill("Browser order update");
  await ticketDialog
    .getByLabel("Customer email", { exact: true })
    .fill("customer@example.com");
  await ticketDialog
    .getByLabel("Customer message", { exact: true })
    .fill(
      "Where is my Northstar order ORD-9999? Please check the shipping policy.",
    );
  await ticketDialog
    .getByRole("button", { name: "Create", exact: true })
    .click();
  await expect(page).toHaveURL(/\/tickets\/[a-f0-9-]+$/);
  const ticketURL = page.url();
  await page
    .getByRole("button", { name: "Prepare draft", exact: true })
    .click();
  const approve = page.getByRole("button", {
    name: "Approve & run mock action",
    exact: true,
  });
  await expect(approve).toBeVisible({ timeout: 90000 });
  await approve.click();
  await expect(
    ui(page).locator(".r-badge.r-executed").first(),
  ).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(ticketURL);
  await expect(
    ui(page).locator(".r-badge.r-executed").first(),
  ).toBeVisible();
  await page.getByRole("link", { name: "Knowledge base", exact: true }).click();
  await createWorkspace(page, bName);
  await expect(ui(page).getByText(filename, { exact: true })).toHaveCount(0);
  await expect(ui(page).locator(".r-answers article")).toHaveCount(0);
  await ui(page)
    .getByLabel("Your question", { exact: true })
    .fill("What is the policy for order changes and cancellations?");
  await page
    .getByRole("button", { name: "Ask with sources", exact: true })
    .click();
  await expect(
    ui(page).locator(".r-answers article > p").first(),
  ).toContainText("could not find supporting information");
  await ui(page)
    .getByRole("combobox", { name: "WORKSPACE", exact: true })
    .selectOption({ label: aName });
  await expect(
    ui(page).getByText(filename, { exact: true }).first(),
  ).toBeVisible();

  const outsiderContext = await browser.newContext({
    baseURL: testInfo.project.use.baseURL,
    viewport: { width: 1280, height: 900 },
    reducedMotion: "reduce",
  });
  try {
    const outsider = await outsiderContext.newPage();
    const outsiderEmail = `agent-${suffix}@example.com`;
    outsider.on("pageerror", (error) => errors.push(error.message));
    await signup(outsider, outsiderEmail, "Invited Agent");
    const own = await createWorkspace(
      outsider,
      "Outside company " + suffix,
      true,
    );
    expect(
      await ui(outsider)
        .getByRole("combobox", { name: "WORKSPACE", exact: true })
        .locator("option")
        .allTextContents(),
    ).not.toContain(aName);
    const headers = { Authorization: `Bearer ${own.access_token}` };
    expect(
      (
        await outsider.request.get(`/api/v1/workspaces/${companyId}/overview`, {
          headers,
        })
      ).status(),
    ).toBe(404);
    expect(
      (
        await outsider.request.get(`/api/v1/documents/${uploadedDocument.id}`, {
          headers,
        })
      ).status(),
    ).toBe(404);
    await page
      .getByRole("link", { name: "Workspace team", exact: true })
      .click();
    await ui(page)
      .getByLabel("Teammate email", { exact: true })
      .fill(outsiderEmail);
    await page
      .getByRole("button", { name: "Create invitation", exact: true })
      .click();
    const invite = ui(page).getByLabel("Invitation link", { exact: true });
    await expect(invite).toBeVisible();
    await outsider.goto(await invite.inputValue());
    await expect(
      outsider.getByRole("button", { name: "Accept invitation", exact: true }),
    ).toBeVisible();
    await outsider
      .getByRole("button", { name: "Accept invitation", exact: true })
      .click();
    await expect(outsider).toHaveURL(/\/knowledge$/);
    await expect(
      ui(outsider).getByText(filename, { exact: true }).first(),
    ).toBeVisible();
    await expect(
      ui(outsider).getByLabel("Upload company document", { exact: true }),
    ).toHaveCount(0);
    await outsider
      .getByRole("link", { name: "Workspace team", exact: true })
      .click();
    await expect(
      outsider.getByRole("button", { name: "Create invitation", exact: true }),
    ).toHaveCount(0);
    await outsider.getByRole("link", { name: "Settings", exact: true }).click();
    await expect(
      ui(outsider).getByLabel("Company workspace name", { exact: true }),
    ).toBeDisabled();
    await page.reload();
    const role = page.getByRole("combobox", {
      name: `Role for ${outsiderEmail}`,
      exact: true,
    });
    await role.selectOption("admin");
    await role
      .locator("..")
      .getByRole("button", { name: "Save role", exact: true })
      .click();
    await outsider.reload();
    await expect(
      ui(outsider).getByLabel("Company workspace name", { exact: true }),
    ).toBeEnabled();
    await expect(
      outsider.getByRole("button", {
        name: "Delete this workspace",
        exact: true,
      }),
    ).toHaveCount(0);
    await page.screenshot({
      path: testInfo.outputPath("workspace-team.png"),
      fullPage: true,
    });
    await page
      .getByRole("link", { name: "Knowledge base", exact: true })
      .click();
    for (const width of [1024, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 960 });
      const layout = await page.evaluate(() => ({
        viewport: innerWidth,
        width: document.documentElement.scrollWidth,
      }));
      expect(layout.width).toBeLessThanOrEqual(layout.viewport + 1);
    }
    await page.screenshot({
      path: testInfo.outputPath("company-knowledge-mobile.png"),
      fullPage: true,
    });
    expect(errors).toEqual([]);
  } finally {
    await outsiderContext.close();
  }
});
