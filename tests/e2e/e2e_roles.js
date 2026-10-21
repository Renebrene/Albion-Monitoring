import { chromium } from "playwright";
import assert from "node:assert/strict";

const BASE = process.env.BASE_URL || "http://127.0.0.1:18001";
const PASSWORD = process.env.E2E_PASSWORD || "TestPass1!";
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROME_PATH || undefined,
  args: ["--no-sandbox"],
});

const results = [];
function pass(name, details = "") {
  results.push({ name, ok: true, details });
  console.log(`PASS ${name}${details ? " :: " + details : ""}`);
}
function fail(name, error) {
  results.push({ name, ok: false, details: error.message });
  console.log(`FAIL ${name} :: ${error.message}`);
}

async function newPage() {
  const page = await browser.newPage({ viewport: { width: 1360, height: 1000 } });
  page.on("dialog", dialog => dialog.accept());
  page.on("pageerror", error => console.log("PAGEERROR:", error.message));
  return page;
}

async function login(page, login, expectedPath) {
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.fill("#login", login);
  await page.fill("#password", PASSWORD);
  await Promise.all([
    page.waitForURL(`${BASE}${expectedPath}`, { timeout: 15000 }),
    page.click('button[type="submit"]'),
  ]);
  await page.waitForTimeout(800);
}

async function tokenOf(page) {
  return page.evaluate(() => localStorage.getItem("token"));
}

async function api(token, path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      Authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let data = {};
  try { data = JSON.parse(text); } catch {}
  if (!response.ok) {
    throw new Error(`${options.method || "GET"} ${path} -> ${response.status}: ${text.slice(0, 200)}`);
  }
  return data;
}

async function feed(token) {
  return (await api(token, "/api/feed?limit=500&offset=0")).items;
}

async function waitForFeedItem(page, type, id, timeout = 15000) {
  const locator = page.locator(`#item-${type}-${id}`);
  await locator.waitFor({ state: "visible", timeout });
  return locator;
}

async function taskByDescription(token, description) {
  const items = await feed(token);
  return items.find(item => item.type === "task" && item.text === description);
}

async function monitoringByText(token, text) {
  const items = await feed(token);
  return items.find(item => item.type === "monitoring" && item.text === text);
}

async function loginRole(role) {
  const page = await newPage();
  const paths = {
    admin: "/admin",
    service: "/client",
    director: "/director",
    crew: "/crew",
    head: "/head",
    staff: "/staff",
    administrator: "/administrator",
    curator: "/curator",
  };
  await login(page, role, paths[role]);
  return page;
}

try {
  // ------------------------------------------------------------------
  // 1. Role landing pages and base-role parity
  // ------------------------------------------------------------------
  {
    const page = await loginRole("staff");
    assert.equal(await page.locator("#monitoringBtn").isVisible(), true);
    assert.equal(await page.locator("#addTaskBtn").isVisible(), false);
    pass("staff lands on service UI", page.url());
    await page.close();
  }
  {
    const page = await loginRole("administrator");
    assert.equal(await page.locator("#settingsToggle").isVisible(), true);
    assert.equal(await page.locator("text=Дать задание").first().isVisible(), true);
    pass("administrator lands on admin UI", page.url());
    await page.close();
  }
  {
    const page = await loginRole("curator");
    assert.equal(await page.locator("text=Дать задание").first().isVisible(), true);
    pass("curator lands on director UI", page.url());
    await page.close();
  }

  // ------------------------------------------------------------------
  // 2. Admin creates task -> service comments and completes -> admin approves
  // ------------------------------------------------------------------
  const admin = await loginRole("admin");
  const adminToken = await tokenOf(admin);
  const adminTaskDescription = `E2E admin task ${Date.now()}`;

  await admin.click("text=Дать задание");
  await admin.waitForSelector("#taskModal.show");
  await admin.waitForFunction(() => document.querySelectorAll("#taskCityList option").length > 0);
  await admin.fill("#taskCity", "Ростов-на-Дону");
  await admin.waitForTimeout(200);
  await admin.fill("#taskAddressName", "Вавилова 59/101");
  await admin.fill("#taskDescription", adminTaskDescription);
  await admin.click("#createTaskBtn");
  await admin.waitForSelector("#taskModal", { state: "hidden", timeout: 15000 });

  const adminTask = await taskByDescription(adminToken, adminTaskDescription);
  assert.ok(adminTask, "admin task created");
  pass("admin creates task", `id=${adminTask.id}`);

  const service = await loginRole("service");
  const serviceToken = await tokenOf(service);
  await waitForFeedItem(service, "task", adminTask.id);
  await service.locator(`#item-task-${adminTask.id}`).click();
  await service.fill(`#input-task-${adminTask.id}`, "Сервис: принял в работу");
  await service.click(`#send-task-${adminTask.id}`);
  await service.waitForFunction(
    id => document.querySelector(`#chat-task-${id}`)?.innerText.includes("принял в работу"),
    adminTask.id,
  );
  pass("service writes task comment");

  await service.locator(`#item-task-${adminTask.id} .client-ready-btn`).click();
  await service.waitForFunction(async () => true);
  await service.waitForTimeout(700);
  let current = await taskByDescription(serviceToken, adminTaskDescription);
  assert.equal(current.workflow_status, "pending_review");
  pass("service sends task to review", current.workflow_status);

  await admin.reload();
  await waitForFeedItem(admin, "task", adminTask.id);
  await admin.locator(`#item-task-${adminTask.id}`).click();
  const adminActions = admin.locator(`#item-task-${adminTask.id} .task-actions button`);
  assert.ok((await adminActions.count()) >= 2, "admin review buttons visible");
  await adminActions.nth(0).click();
  await admin.waitForTimeout(700);
  current = await taskByDescription(adminToken, adminTaskDescription);
  assert.equal(current.workflow_status, "completed");
  assert.equal(
    await admin.locator(`#dc-input-task-${adminTask.id}`).count(),
    0,
    "admin must not render comment form for completed task",
  );
  pass("admin approves task", current.workflow_status);

  // ------------------------------------------------------------------
  // 3. Director/curator: own task, rework, second completion, approval
  // ------------------------------------------------------------------
  for (const role of ["director", "curator"]) {
    const director = await loginRole(role);
    const directorToken = await tokenOf(director);
    const description = `E2E ${role} task ${Date.now()}`;

    await director.click("text=Дать задание");
    await director.waitForSelector("#taskModal.show");
    await director.waitForFunction(() => document.querySelectorAll("#taskCityList option").length > 0);
    await director.fill("#taskCity", "Ростов-на-Дону");
    await director.waitForTimeout(200);
    await director.fill("#taskAddressName", "1-й Конной Армии 32");
    await director.fill("#taskDescription", description);
    await director.click("#createTaskBtn");
    await director.waitForSelector("#taskModal", { state: "hidden", timeout: 15000 });

    const task = await taskByDescription(directorToken, description);
    assert.ok(task, `${role} task created`);
    assert.equal(task.created_by_role, "director");

    await service.reload();
    await waitForFeedItem(service, "task", task.id);
    await service.locator(`#item-task-${task.id} .client-ready-btn`).click();
    await service.waitForTimeout(700);
    current = await taskByDescription(serviceToken, description);
    assert.equal(current.workflow_status, "pending_review");

    await director.reload();
    await waitForFeedItem(director, "task", task.id);
    await director.locator(`#item-task-${task.id}`).click();
    const actions = director.locator(`#item-task-${task.id} .task-actions button`);
    assert.ok((await actions.count()) >= 2, `${role} review buttons visible`);
    await actions.nth(1).click();
    await director.waitForTimeout(700);
    current = await taskByDescription(directorToken, description);
    assert.equal(current.workflow_status, "rework");
    pass(`${role} sends own task to rework`, current.workflow_status);

    await service.reload();
    await waitForFeedItem(service, "task", task.id);
    await service.locator(`#item-task-${task.id} .client-ready-btn`).click();
    await service.waitForTimeout(700);
    current = await taskByDescription(serviceToken, description);
    assert.equal(current.workflow_status, "pending_review");

    await director.reload();
    await waitForFeedItem(director, "task", task.id);
    await director.locator(`#item-task-${task.id}`).click();
    await director.fill(`#dc-input-task-${task.id}`, `${role}: комментарий директора`);
    await director.locator(`#item-task-${task.id} .send-chat-btn`).click();
    await director.waitForFunction(
      id => document.querySelector(`#item-task-${id}`)?.innerText.includes("комментарий директора"),
      task.id,
    );
    pass(`${role} writes director comment`);

    await director.locator(`#item-task-${task.id} .task-actions button`).nth(0).click();
    await director.waitForTimeout(700);
    current = await taskByDescription(directorToken, description);
    assert.equal(current.workflow_status, "completed");
    pass(`${role} approves own task`, current.workflow_status);

    await director.close();
  }

  // Director must not see review buttons on an admin-created task.
  {
    const director = await loginRole("director");
    const description = `E2E scope task ${Date.now()}`;
    await admin.click("text=Дать задание");
    await admin.waitForSelector("#taskModal.show");
    await admin.fill("#taskCity", "Ростов-на-Дону");
    await admin.fill("#taskAddressName", "40 Лет Победы 154");
    await admin.fill("#taskDescription", description);
    await admin.click("#createTaskBtn");
    await admin.waitForSelector("#taskModal", { state: "hidden", timeout: 15000 });
    const task = await taskByDescription(adminToken, description);

    await service.reload();
    await waitForFeedItem(service, "task", task.id);
    await service.locator(`#item-task-${task.id} .client-ready-btn`).click();
    await service.waitForTimeout(700);

    await director.reload();
    await waitForFeedItem(director, "task", task.id);
    await director.locator(`#item-task-${task.id}`).click();
    assert.equal(
      await director.locator(`#item-task-${task.id} .task-actions button`).count(),
      0,
    );
    pass("director cannot review admin task");
    await director.close();
  }

  // ------------------------------------------------------------------
  // 4. Monitoring: service creates, director and admin comment
  // ------------------------------------------------------------------
  {
    const monitoringText = `E2E monitoring ${Date.now()}`;
    await service.reload();
    await service.click("#monitoringBtn");
    await service.waitForSelector("#monitoringModal.active");
    await service.waitForFunction(
      () => typeof serviceMonitoringAddresses !== "undefined" && serviceMonitoringAddresses.length > 0,
    );
    await service.fill("#monCity", "Ростов-на-Дону");
    await service.waitForTimeout(250);
    await service.fill("#monAddrSearch", "Вавилова 59/101");
    await service.waitForTimeout(250);
    assert.notEqual(await service.inputValue("#monAddrId"), "");
    await service.fill("#monText", monitoringText);
    await service.click("#monitoringSubmitBtn");
    await service.waitForSelector("#monitoringModal", { state: "hidden", timeout: 15000 });
    await service.waitForTimeout(500);

    const monitoring = await monitoringByText(serviceToken, monitoringText);
    assert.ok(monitoring, "monitoring created");
    pass("service creates monitoring", `id=${monitoring.id}`);

    const director = await loginRole("director");
    await waitForFeedItem(director, "monitoring", monitoring.id);
    await director.locator(`#item-monitoring-${monitoring.id}`).click();
    await director.fill(`#dc-input-monitoring-${monitoring.id}`, "Директор: мониторинг принят");
    await director.locator(`#item-monitoring-${monitoring.id} .send-chat-btn`).click();
    await director.waitForFunction(
      id => document.querySelector(`#item-monitoring-${id}`)?.innerText.includes("мониторинг принят"),
      monitoring.id,
    );
    pass("director comments monitoring");
    await director.close();

    await admin.reload();
    await waitForFeedItem(admin, "monitoring", monitoring.id);
    await admin.locator(`#item-monitoring-${monitoring.id}`).click();
    await admin.fill(`#dc-input-monitoring-${monitoring.id}`, "Админ: мониторинг проверен");
    await admin.locator(`#item-monitoring-${monitoring.id} .send-chat-btn`).click();
    await admin.waitForFunction(
      id => document.querySelector(`#item-monitoring-${id}`)?.innerText.includes("мониторинг проверен"),
      monitoring.id,
    );
    pass("admin comments monitoring");

    const legacy = await api(adminToken, "/api/monitoring/1");
    assert.ok(
      legacy.some(item => item.is_director_comment && item.text === "Админ: мониторинг проверен"),
      "legacy monitoring endpoint serializes director comment",
    );
    pass("legacy monitoring endpoint returns director comments");
  }

  // ------------------------------------------------------------------
  // 5. Crew report -> head comment -> crew sees it -> admin/director no form
  // ------------------------------------------------------------------
  {
    const crew = await loginRole("crew");
    const crewToken = await tokenOf(crew);
    const reportText = `E2E crew report ${Date.now()}`;

    await crew.waitForFunction(
      () => typeof crewAddresses !== "undefined" && crewAddresses.length > 0,
    );
    await crew.fill("#crewCity", "Батайск");
    await crew.waitForTimeout(250);
    await crew.fill("#crewAddress", "Куйбышева 116");
    await crew.waitForTimeout(250);
    assert.notEqual(await crew.inputValue("#crewAddressId"), "");
    await crew.fill("#crewText", reportText);
    await crew.click("#sendCrewReport");

    let report = null;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      const reports = await api(crewToken, "/api/crew/reports");
      report = reports.find(item => item.text === reportText);
      if (report) break;
      await new Promise(resolve => setTimeout(resolve, 300));
    }
    assert.ok(report, "crew report created");
    pass("crew creates report", `id=${report.id}`);

    await crew.reload();
    await crew.locator(`[data-report-toggle="${report.id}"]`).waitFor({ timeout: 15000 });
    await crew.locator(`[data-report-toggle="${report.id}"]`).click();
    await crew.waitForFunction(
      id => document.querySelector(`#report-details-${id}`)?.innerText.includes("E2E crew report"),
      report.id,
    );

    const head = await loginRole("head");
    await head.locator(`[data-report-toggle="${report.id}"]`).waitFor({ timeout: 15000 });
    await head.locator(`[data-report-toggle="${report.id}"]`).click();
    await head.fill(`#comment-text-${report.id}`, "Руководитель: отчёт просмотрен");
    await head.click(`#comment-send-${report.id}`);
    await head.waitForFunction(
      id => document.querySelector(`#comments-${id}`)?.innerText.includes("отчёт просмотрен"),
      report.id,
    );
    pass("head comments crew report");
    await head.close();

    await crew.reload();
    await crew.locator(`[data-report-toggle="${report.id}"]`).waitFor({ timeout: 15000 });
    await crew.locator(`[data-report-toggle="${report.id}"]`).click();
    await crew.waitForFunction(
      id => document.querySelector(`#report-details-${id}`)?.innerText.includes("отчёт просмотрен"),
      report.id,
    );
    const crewDetails = await crew.locator(`#report-details-${report.id}`).innerText();
    assert.ok(crewDetails.includes("Руководитель"), "head comment labelled as Руководитель");
    pass("crew sees head comment");

    await admin.reload();
    await waitForFeedItem(admin, "crew_report", report.id);
    await admin.locator(`#item-crew_report-${report.id}`).click();
    assert.equal(await admin.locator(`#dc-input-crew_report-${report.id}`).count(), 0);
    assert.ok(
      (await admin.locator(`#item-crew_report-${report.id}`).innerText()).includes("доступны Руководителю"),
    );
    pass("admin has no crew comment form");

    const curator = await loginRole("curator");
    await waitForFeedItem(curator, "crew_report", report.id);
    await curator.locator(`#item-crew_report-${report.id}`).click();
    assert.equal(await curator.locator(`#dc-input-crew_report-${report.id}`).count(), 0);
    pass("curator sees crew report without comment form");
    await curator.close();
    await crew.close();
  }

  // ------------------------------------------------------------------
  // 6. Administrator/curator parity for management UI
  // ------------------------------------------------------------------
  {
    const administrator = await loginRole("administrator");
    await administrator.click("#settingsToggle");
    await administrator.waitForSelector("#settingsPanel.open");
    await administrator.waitForTimeout(700);
    const rows = await administrator.locator("#usersList .user-row").count();
    assert.ok(rows >= 8, `expected >=8 users, got ${rows}`);
    const roles = await administrator.locator("#newUserRole option").evaluateAll(options =>
      options.map(option => option.value),
    );
    for (const role of ["staff", "administrator", "curator"]) {
      assert.ok(roles.includes(role), `missing role option ${role}`);
    }
    pass("administrator sees settings and new roles", `users=${rows}`);
    await administrator.close();
  }

  console.log(`\nRESULT ${results.filter(item => item.ok).length}/${results.length} passed`);
  await browser.close();
  if (results.some(item => !item.ok)) process.exit(1);
} catch (error) {
  console.error("E2E FAILED:", error);
  await browser.close();
  process.exit(1);
}
