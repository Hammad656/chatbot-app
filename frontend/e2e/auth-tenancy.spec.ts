import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
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

test("customer order chat, human approval, actual portal delivery and visible roles", async ({
  page,
  browser,
}, testInfo) => {
  let embedHTML = "";
  const embedServer = createServer((_request, response) => {
    response.setHeader("Content-Type", "text/html");
    response.end(embedHTML);
  });
  await new Promise<void>((resolve, reject) => {
    embedServer.once("error", reject);
    embedServer.listen(0, "127.0.0.1", resolve);
  });
  const embedOrigin = `http://127.0.0.1:${(embedServer.address() as AddressInfo).port}`;
  try {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await signup(
      page,
      `portal-owner-${unique()}@example.com`,
      "Portal Company Owner",
    );
    await createWorkspace(page, "Aurora customer support " + unique(), true);
    await expect(
      ui(page).getByLabel("Signed in as owner", { exact: true }),
    ).toBeVisible();
    await expect(
      ui(page).getByRole("button", { name: "Log out", exact: true }),
    ).toBeVisible();
    await ui(page)
      .getByRole("button", { name: "Load demo data", exact: true })
      .click();
    await ui(page).getByRole("link", { name: "Settings", exact: true }).click();
    await ui(page).getByLabel("Enable customer chat for this company").check();
    await ui(page).getByLabel("Allowed website origins").fill(embedOrigin);
    await ui(page)
      .getByRole("button", { name: "Save settings", exact: true })
      .click();
    await expect(
      ui(page).getByText("Chat is enabled", { exact: true }),
    ).toBeVisible();
    const supportPath = await ui(page)
      .getByRole("link", { name: "Open customer chat" })
      .getAttribute("href");
    expect(supportPath).toMatch(/^\/widget\//);
    await ui(page)
      .getByLabel("Customer name", { exact: true })
      .fill("Alex Customer");
    await ui(page)
      .getByLabel("Customer email", { exact: true })
      .fill("alex@example.com");
    await ui(page)
      .getByRole("button", { name: "Create private access link", exact: true })
      .click();
    const privateLink = await ui(page)
      .getByLabel("Private customer link", { exact: true })
      .inputValue();
    expect(privateLink).toContain("#access=");
    const customerContext = await browser.newContext();
    const customer = await customerContext.newPage();
    customer.on("pageerror", (error) => errors.push(error.message));
    await customer.goto(privateLink);
    await expect(
      customer.getByText("Customer · Signed in", { exact: true }),
    ).toBeVisible();
    await expect(customer).not.toHaveURL(/#access=/);
    await customer
      .getByLabel("Your message", { exact: true })
      .fill("Where is my order?");
    await customer
      .getByRole("button", { name: "Send message", exact: true })
      .click();
    await expect(customer.locator(".customer-assistant").last()).toContainText(
      "share your order reference",
      { timeout: 90000 },
    );
    await customer
      .getByLabel("Your message", { exact: true })
      .fill("Where is my order ORD-1001?");
    await customer
      .getByRole("button", { name: "Send message", exact: true })
      .click();
    await expect(
      customer.getByText("Your support team is on it.", { exact: true }),
    ).toBeVisible({ timeout: 90000 });
    await expect(customer.locator(".customer-agent")).toHaveCount(0);
    await ui(page)
      .getByRole("link", { name: "Conversations", exact: true })
      .click();
    await ui(page)
      .getByRole("button", { name: /Where is my order ORD-1001/ })
      .click();
    await ui(page)
      .getByRole("link", { name: /Open support ticket/ })
      .click();
    const approve = ui(page).getByRole("button", {
      name: "Approve & deliver reply",
      exact: true,
    });
    await expect(approve).toBeVisible({ timeout: 90000 });
    await expect(
      ui(page).getByLabel("Reply for review", { exact: true }),
    ).toHaveValue(/shipped/);
    await page.screenshot({
      path: testInfo.outputPath("staff-order-review.png"),
      fullPage: true,
    });
    await approve.click();
    await expect(customer.locator(".customer-agent").last()).toContainText(
      "shipped",
      { timeout: 60000 },
    );
    await expect(ui(page).locator(".r-badge.r-replied").first()).toBeVisible();
    await customer
      .locator(".customer-agent")
      .last()
      .getByRole("button", { name: "Yes", exact: true })
      .click();
    await expect(
      customer
        .locator(".customer-agent")
        .last()
        .getByRole("button", { name: "Yes", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await customer.reload();
    await expect(
      customer.getByText("Customer · Signed in", { exact: true }),
    ).toBeVisible();
    await expect(customer.locator(".customer-agent")).toContainText("shipped");
    expect(await customer.locator(".customer-agent").count()).toBe(1);
    const stored = await customer.evaluate(() =>
      [...Object.values(localStorage), ...Object.values(sessionStorage)].join(
        " ",
      ),
    );
    expect(stored).not.toMatch(/eyJ[A-Za-z0-9_-]+\./);
    for (const width of [1440, 768, 390, 320]) {
      await customer.setViewportSize({ width, height: 900 });
      await expect(
        customer.getByRole("button", { name: "Log out", exact: true }),
      ).toBeVisible();
      expect(
        await customer.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth + 1,
        ),
      ).toBe(true);
    }
    await customer.screenshot({
      path: testInfo.outputPath("customer-order-mobile.png"),
      fullPage: true,
    });
    const secondCustomer = await customerContext.newPage();
    await secondCustomer.goto(new URL(supportPath!, privateLink).toString());
    await expect(
      secondCustomer.getByText("Customer · Signed in", { exact: true }),
    ).toBeVisible();
    await customer
      .getByRole("button", { name: "Log out", exact: true })
      .click();
    await expect(
      customer.getByRole("heading", {
        name: "You’re safely signed out.",
        exact: true,
      }),
    ).toBeVisible();
    await expect(customer.getByText("shipped")).toHaveCount(0);
    await expect(
      secondCustomer.getByRole("heading", {
        name: "You’re safely signed out.",
        exact: true,
      }),
    ).toBeVisible();
    await expect(secondCustomer.locator(".customer-agent")).toHaveCount(0);
    const guestContext = await browser.newContext();
    const guest = await guestContext.newPage();
    await guest.goto(new URL(supportPath!, privateLink).toString());
    await expect(
      guest.getByText("Guest customer", { exact: true }),
    ).toBeVisible();
    await guest
      .getByLabel("Your message", { exact: true })
      .fill("Where is my order ORD-1001?");
    await guest
      .getByRole("button", { name: "Send message", exact: true })
      .click();
    await expect(guest.locator(".customer-assistant").last()).toContainText(
      "private customer access link",
      { timeout: 90000 },
    );
    const framePolicy = await page.request.get(supportPath!);
    expect(framePolicy.headers()["content-security-policy"]).toContain(
      embedOrigin,
    );
    expect(framePolicy.headers()["content-security-policy"]).not.toContain(
      "unapproved.example",
    );
    const embed = await guestContext.newPage();
    const appOrigin = new URL(privateLink).origin;
    const key = supportPath!.split("/").pop();
    embedHTML = `<html><head><title>Company widget test</title></head><body><h1>Company website</h1><script src="${appOrigin}/widget.js" data-workspace="${key}" defer></script></body></html>`;
    await embed.goto(embedOrigin);
    await embed
      .getByRole("button", { name: "Open customer support", exact: true })
      .click();
    await expect(
      embed
        .frameLocator('iframe[title="Customer support conversation"]')
        .getByText("Guest customer", { exact: true }),
    ).toBeVisible();
    await ui(page)
      .getByRole("link", { name: "Approvals", exact: true })
      .click();
    await ui(page)
      .getByRole("combobox", { name: "Action status", exact: true })
      .selectOption("succeeded");
    await expect(
      ui(page).getByRole("heading", {
        name: "Send a reviewed reply",
        exact: true,
      }),
    ).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath("approvals-desktop.png"),
      fullPage: true,
    });
    await ui(page)
      .getByRole("button", { name: "Log out", exact: true })
      .click();
    await expect(page).toHaveURL(/\/login$/);
    expect(errors).toEqual([]);
    await customerContext.close();
    await guestContext.close();
  } finally {
    await new Promise<void>((resolve) => embedServer.close(() => resolve()));
  }
});

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
  await page.getByRole("button", { name: "Log out", exact: true }).click();
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
  await expect(page).toHaveURL(/\/settings$/);
  // Next.js retains hidden routes with the same header. Wait for the destination
  // before resolving its logout control so a transition cannot retain the old handle.
  await expect(
    ui(page).getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
  await ui(page).getByRole("button", { name: "Log out", exact: true }).click();
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
    name: "Approve & record reply",
    exact: true,
  });
  await expect(approve).toBeVisible({ timeout: 90000 });
  await approve.click();
  await expect(ui(page).locator(".r-badge.r-executed").first()).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(ticketURL);
  await expect(ui(page).locator(".r-badge.r-executed").first()).toBeVisible();
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
