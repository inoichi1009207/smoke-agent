const { test, expect } = require('@playwright/test');
const { click, fill, check, choose, hover, press, see, notSee, seeOnce, region } = require('../e2e-support/outsider');

const APP = process.env.APP_URL;

async function failure(promise) {
  try { await promise; return ''; } catch (error) { return String(error.message).split('\n')[0]; }
}

test.describe('store', () => {
  for (let i = 0; i < 3; i += 1) {
    test(`a store page is complete at once #${i}`, async ({ page }) => {
      await page.goto(APP + '/');
      await click(page, 'Store shelves');
      await see(page, 'Shelf A');
    });
  }
  test('a target with a name and no role is looked up by its name', async ({ page }) => {
    await page.goto(APP + '/');
    await click(page, { name: 'Store shelves' });
    await see(page, { name: 'Shelf A' });
    expect(await failure(see(page, { name: 'No such shelf' }))).toMatch(/No such shelf/);
  });
  test('a page that fetches on mount is judged late', async ({ page }) => {
    await page.goto(APP + '/');
    await click(page, 'Fetch shelves');
    expect(await failure(see(page, 'Shelf A'))).toMatch(/not on the page when it opened/);
  });
  test('login refreshes what needs a signed-in user; logout forgets it', async ({ page }) => {
    await page.goto(APP + '/');
    await click(page, 'Private');
    await see(page, 'Not loaded');
    await notSee(page, 'Null cached');
    await click(page, 'Login');
    await see(page, 'Test User');
    await click(page, 'Private');
    await see(page, 'Secret A');
    await click(page, 'Logout');
    await see(page, 'Login');
    await click(page, 'Private');
    await see(page, 'Not loaded');
  });
  test('a synchronous sign-in survives an immediate navigation; an awaited one does not', async ({ page }) => {
    await page.goto(APP + '/');
    await page.getByRole('button', { name: 'Login now', exact: true }).click();
    await expect(page.getByText('Test User')).toBeVisible({ timeout: 100 });
    await page.goto(APP + '/private');
    await see(page, 'Secret A');
    await page.context().clearCookies();
    await page.goto(APP + '/');
    await page.getByRole('button', { name: 'Login', exact: true }).click();
    await page.goto(APP + '/private');
    await see(page, 'Not loaded');
  });
  test('a request of the previous user cannot refill the store after logout', async ({ page }) => {
    await page.goto(APP + '/');
    await click(page, 'Login');
    await see(page, 'Test User');
    await click(page, 'Logout while loading');
    await see(page, 'Login');
    await page.waitForTimeout(1200);
    await click(page, 'Private');
    await see(page, 'Not loaded');
  });
  test('a change is refreshed before navigating', async ({ page }) => {
    await page.goto(APP + '/');
    await click(page, 'New');
    await click(page, 'Save item');
    await see(page, 'Shelf New');
  });
  test('the refresh after a change is a new request, not the one already under way', async ({ page }) => {
    await page.goto(APP + '/');
    await click(page, 'New');
    await click(page, 'Save while loading');
    await page.waitForURL('**/store');
    await expect(page.getByText('Shelf Racing')).toBeVisible({ timeout: 1000 });
  });
  for (const [button, expected] of [['Reset then load', 'reloaded: yes'], ['Refresh across reset', 'refresh: superseded'],
    ['Refresh private signed out', 'private refresh: rejected']]) {
    test(`store race: ${button} (audit r17)`, async ({ page }) => {
      await page.goto(APP + '/');
      await click(page, 'Lab');
      await click(page, button);
      await see(page, expected);
    });
  }
  test('a failed refresh does not navigate', async ({ page }) => {
    await page.goto(APP + '/');
    await click(page, 'New');
    await click(page, 'Save broken');
    await see(page, 'Save failed');
    expect(new URL(page.url()).pathname).toBe('/new');
  });
});

function html(body, script) {
  return `<!doctype html><body>${body}<main id="m"></main><script>${script}</script></body>`;
}
const LIST = "document.getElementById('m').innerHTML = '<p>Shelf A</p>';";

test.describe('helper', () => {
  test('a hash change is the same page: not judged', async ({ page }) => {
    await page.route('http://h.test/**', (r) => r.fulfill({ contentType: 'text/html', body: html('<a id="t" href="#part">Part</a>',
      `document.getElementById('t').onclick = () => setTimeout(() => { ${LIST} }, 300);`) }));
    await page.goto('http://h.test/');
    await click(page, 'Part');
    await see(page, 'Shelf A');
  });
  test('press does not swallow the first look of the next lookup', async ({ page }) => {
    await page.route('http://h.test/**', (r) => r.fulfill({ contentType: 'text/html', body: html('<a id="t" href="/list">List</a>',
      `document.getElementById('t').onclick = (e) => { e.preventDefault(); history.pushState({}, '', '/list'); setTimeout(() => { ${LIST} }, 300); };`) }));
    await page.goto('http://h.test/');
    await click(page, 'List');
    await press(page, 'Shift');
    expect(await failure(see(page, 'Shelf A'))).toMatch(/not on the page when it opened/);
  });
  test('two pages do not share a pending judgement', async ({ browser }) => {
    const context = await browser.newContext();
    const a = await context.newPage();
    const b = await context.newPage();
    for (const p of [a, b]) {
      await p.route('http://h.test/**', (r) => r.fulfill({ contentType: 'text/html', body: html('<a id="t" href="/list">List</a><p>Other</p>',
        `document.getElementById('t').onclick = (e) => { e.preventDefault(); history.pushState({}, '', '/list'); setTimeout(() => { ${LIST} }, 300); };`) }));
      await p.goto('http://h.test/');
    }
    await click(a, 'List');
    await see(b, 'Other');
    expect(await failure(see(a, 'Shelf A'))).toMatch(/not on the page when it opened/);
    await context.close();
  });
  test('checkbox and switch', async ({ page }) => {
    await page.setContent('<label><input type="checkbox"> Work</label>' +
      '<button role="switch" aria-checked="false" onclick="this.setAttribute(\'aria-checked\', String(this.getAttribute(\'aria-checked\') !== \'true\'))">Dark mode</button>');
    const box = page.getByRole('checkbox', { name: 'Work' });
    await check(page, { role: 'checkbox', name: 'Work' });
    await expect(box).toBeChecked();
    await fill(page, { role: 'checkbox', name: 'Work' }, false);
    await expect(box).not.toBeChecked();
    await fill(page, { role: 'switch', name: 'Dark mode' }, true);
    await expect(page.getByRole('switch', { name: 'Dark mode' })).toHaveAttribute('aria-checked', 'true');
    await fill(page, { role: 'switch', name: 'Dark mode' }, true);
    await expect(page.getByRole('switch', { name: 'Dark mode' })).toHaveAttribute('aria-checked', 'true');
    await check(page, { role: 'switch', name: 'Dark mode' }, false);
    await expect(page.getByRole('switch', { name: 'Dark mode' })).toHaveAttribute('aria-checked', 'false');
  });
  test('the whole name still wins over a loose match already on the page (ADR-065)', async ({ page }) => {
    await page.setContent('<p role="status">Note trashed</p><div id="m"></div><p id="out"></p><script>' +
      "setTimeout(() => { document.getElementById('m').innerHTML = '<button onclick=\"document.getElementById(\\'out\\').textContent = \\'button\\'\">Trash</button>'; }, 150);" +
      '</script>');
    await click(page, 'Trash');
    await expect(page.locator('#out')).toHaveText('button');
  });
  test('clicks that only match loosely fit the 10 s test timeout (ADR-085)', async ({ page }) => {
    await page.setContent('<button onclick="this.dataset.n = String(Number(this.dataset.n || 0) + 1)">Save note</button>');
    const begun = Date.now();
    for (let i = 0; i < 6; i += 1) await click(page, 'Save');
    expect(Date.now() - begun).toBeLessThan(4000);
    await expect(page.getByRole('button', { name: 'Save note' })).toHaveAttribute('data-n', '6');
  });
  test('a text match already on the page does not shorten the wait for the whole name (audit r17)', async ({ page }) => {
    await page.setContent('<p role="status">Note trashed</p><div id="m"></div><p id="out"></p><script>' +
      "setTimeout(() => { document.getElementById('m').innerHTML = '<button onclick=\"document.getElementById(\\'out\\').textContent = \\'button\\'\">Trash</button>'; }, 600);" +
      '</script>');
    await click(page, 'Trash');
    await expect(page.locator('#out')).toHaveText('button');
  });
  test('a click that follows a real <a href> (a full load) leaves nothing to judge (audit r17)', async ({ page }) => {
    await page.route('http://h.test/**', (r) => r.fulfill({ contentType: 'text/html', body: new URL(r.request().url()).pathname === '/list'
      ? html('', `setTimeout(() => { ${LIST} }, 300);`)
      : html('<a href="/list">List</a>', '') }));
    await page.goto('http://h.test/');
    await click(page, 'List');
    await see(page, 'Shelf A');
  });
  test('a query change is the same page: not judged', async ({ page }) => {
    await page.route('http://h.test/**', (r) => r.fulfill({ contentType: 'text/html', body: html('<a id="t" href="?sort=1">Sort</a>',
      `document.getElementById('t').onclick = (e) => { e.preventDefault(); history.pushState({}, '', '?sort=1'); setTimeout(() => { ${LIST} }, 300); };`) }));
    await page.goto('http://h.test/');
    await click(page, 'Sort');
    await see(page, 'Shelf A');
  });
  test('a read-only placeholder is not a field at the first look', async ({ page }) => {
    await page.route('http://h.test/**', (r) => r.fulfill({ contentType: 'text/html', body: html('<a id="t" href="/form">Form</a>',
      `document.getElementById('t').onclick = (e) => { e.preventDefault(); history.pushState({}, '', '/form');` +
      ` document.getElementById('m').innerHTML = '<label>Title <input readonly></label>';` +
      ` setTimeout(() => { document.getElementById('m').innerHTML = '<label>Title <input></label>'; }, 300); };`) }));
    await page.goto('http://h.test/');
    await click(page, 'Form');
    expect(await failure(fill(page, 'Title', 'x'))).toMatch(/not on the page when it opened/);
  });
  test('a radio button cannot be unselected', async ({ page }) => {
    await page.setContent('<label><input type="radio" name="s" checked> Small</label>');
    expect(await failure(check(page, { role: 'radio', name: 'Small' }, false))).toMatch(/radio button/);
  });
  test('a full page load clears a pending judgement', async ({ page }) => {
    await page.route('http://h.test/**', (r) => r.fulfill({ contentType: 'text/html', body: html('<a id="t" href="/list">List</a>',
      `document.getElementById('t').onclick = (e) => { e.preventDefault(); history.pushState({}, '', '/list'); };` +
      `if (location.pathname === '/list') setTimeout(() => { ${LIST} }, 300);`) }));
    await page.goto('http://h.test/');
    await click(page, 'List');
    await page.goto('http://h.test/');
    await page.goto('http://h.test/list');
    await see(page, 'Shelf A');
  });
});

test.describe('list item titles', () => {
  test('the control nested in the heading is one match for the bare name, and click hits the control', async ({ page }) => {
    await page.goto(APP + '/list');
    await seeOnce(page, 'Book 5');
    await click(page, 'Book 5');
    await see(page, 'opened: Book 5');
  });
  test('the same words rendered twice fail "exactly one"', async ({ page }) => {
    await page.goto(APP + '/list');
    expect(await failure(seeOnce(page, 'Book 6'))).toMatch(/2 are visible/);
  });
});

test('an edit made during a load is not overwritten by the older answer', async ({ page }) => {
  await page.goto(APP + '/lab');
  await page.getByRole('button', { name: 'Edit while loading' }).click();
  await expect(page.getByText('after edit: Shelf Edited')).toBeVisible({ timeout: 3000 });
});

test('a put into a path never loaded does not make it fresh; into a loaded path it still does', async ({ page }) => {
  await page.goto(APP + '/lab');
  await page.getByRole('button', { name: 'Put partial then open' }).click();
  await expect(page.getByText('Shelf A')).toBeVisible({ timeout: 1000 });
  await expect(page.getByText('Partial answer')).toHaveCount(0);
  await page.goto(APP + '/lab');
  await page.waitForTimeout(2300);
  await page.getByRole('button', { name: 'Put edit then reopen' }).click();
  await expect(page.getByText('Shelf Optimistic')).toBeVisible({ timeout: 300 });
  await page.waitForTimeout(700);
  await expect(page.getByText('Shelf Optimistic')).toBeVisible({ timeout: 100 });
});

test.describe('named gestures', () => {
  const PAGE = '<div role="grid" aria-label="Worksheet grid"><div role="row" aria-label="1">' +
    '<span role="gridcell" aria-label="A1">1</span><span role="gridcell" aria-label="B2">2</span><span role="gridcell" aria-label="C3">3</span></div></div>' +
    '<p id="m">nothing yet</p><label>Import CSV <input id="f" type="file"></label>' +
    '<button id="pick">Choose file</button><input id="hidden" type="file" style="display:none">' +
    '<a id="d" download="Sheet1.csv" href="data:text/csv,name%2Cage%0AAda%2C36">Export CSV</a><button id="c">Copy</button>' +
    '<div role="tablist"><button role="tab" aria-selected="true">Sheet1</button><button role="tab" aria-selected="false">Sheet2</button></div>';
  const SCRIPT = "const m = document.getElementById('m'); let start = null;" +
    "document.querySelectorAll('[role=gridcell]').forEach((c) => {" +
    "  c.ondblclick = () => { m.textContent = 'editing ' + c.getAttribute('aria-label'); };" +
    "  c.oncontextmenu = (e) => { e.preventDefault(); m.textContent = 'menu for ' + c.getAttribute('aria-label'); };" +
    "  c.onmousedown = () => { start = c.getAttribute('aria-label'); };" +
    "  c.onmouseup = () => { if (start && start !== c.getAttribute('aria-label')) m.textContent = 'selected ' + start + ':' + c.getAttribute('aria-label'); };" +
    "});" +
    "document.getElementById('f').onchange = async (e) => { const f = e.target.files[0]; m.textContent = f.name + ' ' + (await f.text()).split('\\n')[0]; };" +
    "document.getElementById('pick').onclick = () => document.getElementById('hidden').click();" +
    "document.getElementById('hidden').onchange = (e) => { m.textContent = 'picked ' + e.target.files[0].name; };" +
    "document.getElementById('c').onclick = async () => { await navigator.clipboard.writeText('https://example.test/repo.git'); m.textContent = 'Copied'; };" +
    "document.addEventListener('paste', (e) => { m.textContent = 'pasted ' + e.clipboardData.getData('text/plain').split(/\\r?\\n/).join('|').replace(/\\t/g, '~'); });";
  async function open(page) {
    await page.route('http://localhost:59999/**', (r) => r.fulfill({ contentType: 'text/html', body: html(PAGE, SCRIPT) }));
    await page.goto('http://localhost:59999/');
  }
  test('double click and right click reach the named cell', async ({ page }) => {
    const { doubleClick, rightClick } = require('../e2e-support/outsider');
    await open(page);
    await doubleClick(page, { role: 'gridcell', name: 'B2' });
    await see(page, 'editing B2');
    await rightClick(page, { role: 'gridcell', name: 'C3' });
    await see(page, 'menu for C3');
  });
  test('a target whose role is no ARIA role is found by its label', async ({ page }) => {
    await page.setContent('<form><label>Username or email <input name="u"></label>' +
      '<label for="p">Password</label><input id="p" type="password"><button type="button">Sign in</button></form>');
    await fill(page, { role: 'password', name: 'Password' }, 'Valid-password-123!');
    await fill(page, { role: 'input', name: 'Username or email' }, 'alice-dev');
    expect(await page.locator('#p').inputValue()).toBe('Valid-password-123!');
    expect(await page.locator('input[name="u"]').inputValue()).toBe('alice-dev');
    await fill(page, { role: 'textbox', name: 'Username or email' }, 'bob');
    expect(await page.locator('input[name="u"]').inputValue()).toBe('bob');
    expect(await failure(click(page, { role: 'btn', name: 'Sign in' }))).toMatch(/SPEC DEFECT: "btn" is no ARIA role/);
  });
  test('drag presses on one cell and releases on another', async ({ page }) => {
    const { drag } = require('../e2e-support/outsider');
    await open(page);
    await drag(page, { role: 'gridcell', name: 'A1' }, { role: 'gridcell', name: 'C3' });
    await see(page, 'selected A1:C3');
  });
  test('drag moves in several steps with pauses between press and release', async ({ page }) => {
    const { drag } = require('../e2e-support/outsider');
    await page.setContent('<div style="display:flex;gap:40px"><div role="gridcell" aria-label="A1" style="width:60px;height:30px">1</div>' +
      '<div role="gridcell" aria-label="B1" style="width:60px;height:30px">2</div>' +
      '<div role="gridcell" aria-label="C1" style="width:60px;height:30px">3</div></div><p id="m">none</p><script>' +
      "let down = 0, moves = 0, over = [];" +
      "document.addEventListener('mousedown', () => { down = performance.now(); moves = 0; over = []; });" +
      "document.addEventListener('mousemove', (e) => { if (!down) return; moves += 1; const c = e.target.getAttribute('aria-label');" +
      "  if (c && over[over.length - 1] !== c) over.push(c); });" +
      "document.addEventListener('mouseup', () => { document.getElementById('m').textContent = 'moves ' + moves + ' ms ' +" +
      "  Math.round(performance.now() - down) + ' over ' + over.join(','); down = 0; });" +
      '</script>');
    await drag(page, { role: 'gridcell', name: 'A1' }, { role: 'gridcell', name: 'C1' });
    const [, moves, ms, over] = (await page.locator('#m').textContent()).match(/moves (\d+) ms (\d+) over (.*)/);
    expect(Number(moves)).toBeGreaterThanOrEqual(6);
    expect(Number(ms)).toBeGreaterThanOrEqual(200);
    expect(over.split(',')).toEqual(['A1', 'B1', 'C1']);
  });
  test('two range drags in a row each select their own range', async ({ page }) => {
    const { drag } = require('../e2e-support/outsider');
    const GRID = '<div role="grid" aria-label="Sheet" style="user-select:none">' + ['A1', 'B1', 'C1', 'D1'].map((c) =>
      `<span role="gridcell" aria-label="${c}" style="display:inline-block;width:60px;height:30px">${c} text</span>`).join('') +
      '</div><p id="m">none</p><script>' +
      "let start = null, end = null;" +
      "document.querySelectorAll('[role=gridcell]').forEach((c) => {" +
      "  c.onmousedown = () => { start = end = c.getAttribute('aria-label'); };" +
      "  c.onmouseenter = () => { if (start) end = c.getAttribute('aria-label'); };" +
      "});" +
      "document.addEventListener('mouseup', () => { if (start) document.getElementById('m').textContent = 'selected ' + start + ':' + end; start = null; });" +
      '</script>';
    await page.setContent(GRID);
    await drag(page, { role: 'gridcell', name: 'A1' }, { role: 'gridcell', name: 'C1' });
    await expect(page.locator('#m')).toHaveText('selected A1:C1');
    await drag(page, { role: 'gridcell', name: 'B1' }, { role: 'gridcell', name: 'D1' });
    await expect(page.locator('#m')).toHaveText('selected B1:D1');
  });
  test('drag moves a draggable card onto a column (HTML drag and drop)', async ({ page }) => {
    const { drag } = require('../e2e-support/outsider');
    await page.setContent('<div id="card" draggable="true" style="width:80px;height:30px">Card 1</div>' +
      '<section id="col" role="region" aria-label="Done" style="margin-top:80px;width:200px;height:100px;border:1px solid #000">Done</section>' +
      '<p id="m">nowhere</p><script>' +
      "const col = document.getElementById('col');" +
      "document.getElementById('card').ondragstart = (e) => e.dataTransfer.setData('text/plain', 'card');" +
      "col.ondragover = (e) => e.preventDefault();" +
      "col.ondrop = (e) => { e.preventDefault(); document.getElementById('m').textContent = 'dropped ' + e.dataTransfer.getData('text/plain'); };" +
      '</script>');
    await drag(page, 'Card 1', { role: 'region', name: 'Done' });
    await expect(page.locator('#m')).toHaveText('dropped card');
  });
  test('choose with a stated combobox role refuses a button and takes a real select', async ({ page }) => {
    await page.setContent('<button aria-label="Role" aria-haspopup="listbox" onclick="document.getElementById(\'l\').hidden = false">Role: Member</button>' +
      '<div id="l" role="listbox" hidden><div role="option">Member</div><div role="option">Owner</div></div>');
    expect(await failure(choose(page, { role: 'combobox', name: 'Role' }, 'Owner'))).toMatch(/No dropdown named combobox named Role/);
    await page.setContent('<label>Role <select id="r"><option>Member</option><option>Owner</option></select></label>');
    await choose(page, { role: 'combobox', name: 'Role' }, 'Owner');
    expect(await page.locator('#r').inputValue()).toBe('Owner');
  });
  test('upload fills a labelled file field, or the chooser a button opens', async ({ page }) => {
    const { upload } = require('../e2e-support/outsider');
    await open(page);
    await upload(page, 'Import CSV', 'people.csv', 'name,age\nAda,36\n');
    await see(page, 'people.csv name,age');
    await upload(page, 'Choose file', 'other.csv', 'x\n');
    await see(page, 'picked other.csv');
  });
  test('download returns the file name and its text; the clipboard is read back', async ({ page }) => {
    const { download, allowClipboard, clipboardText } = require('../e2e-support/outsider');
    await open(page);
    const file = await download(page, 'Export CSV');
    expect(file.name).toBe('Sheet1.csv');
    expect(file.text).toContain('Ada,36');
    await allowClipboard(page);
    await click(page, 'Copy');
    await see(page, 'Copied');
    expect(await clipboardText(page)).toBe('https://example.test/repo.git');
  });
  test('putClipboard + Control+V reaches the page as a paste event', async ({ page }) => {
    const { putClipboard } = require('../e2e-support/outsider');
    await open(page);
    await putClipboard(page, 'Alpha\t3\nBeta\t4');
    await click(page, { role: 'gridcell', name: 'B2' });
    await press(page, 'Control+V');
    await see(page, 'pasted Alpha~3|Beta~4');
  });
  test('tagged() finds elements by their HTML tag, not by a text that contains the word', async ({ page }) => {
    const { tagged } = require('../e2e-support/outsider');
    await page.route('http://tag.test/**', (r) => r.fulfill({ contentType: 'text/html',
      body: '<p>a bookmark remark</p><p>Study <mark>st</mark>ep</p>' }));
    await page.goto('http://tag.test/');
    await expect(tagged(page, 'mark')).toHaveCount(1);
    await expect(tagged(page, 'mark').first()).toHaveText('st');
    expect(() => tagged(page, 'mark, p')).toThrow(/SPEC DEFECT/);
  });
  test('region() refuses a name given as its role', async ({ page }) => {
    await open(page);
    expect(() => region(page, 'Account menu')).toThrow(/SPEC DEFECT: region\(\) takes a ROLE first/);
    await expect(region(page, 'tablist')).toBeVisible();
  });
  test('locate finds a named element to check a stated state on', async ({ page }) => {
    const { locate } = require('../e2e-support/outsider');
    await open(page);
    await expect(locate(page, { role: 'tab', name: 'Sheet2' })).toHaveAttribute('aria-selected', 'false');
    await expect(await locate(page, { role: 'tab', name: 'Sheet1' })).toHaveAttribute('aria-selected', 'true');
    expect(await locate(page, 'Sheet1').first().textContent()).toBe('Sheet1');
    await expect(locate(page, { role: 'tab', name: 'Sheet9' })).toHaveCount(0);
  });
});

test.describe('scoped dropdown and hover', () => {
  const PAGE = '<label>Country <select id="outer"><option>Norway</option><option>Canada</option></select></label>' +
    '<section aria-label="Editor"><label>Country <select id="inner"><option>Norway</option><option>Canada</option></select></label>' +
    '<p id="h">Invoice 1042</p></section><p id="o">Invoice 1042</p>';
  const SCRIPT = "document.getElementById('h').onmouseenter = () => { document.getElementById('m').textContent = 'hovered inner'; };" +
    "document.getElementById('o').onmouseenter = () => { document.getElementById('m').textContent = 'hovered outer'; };";
  test('choose inside a scope changes only the box in that scope', async ({ page }) => {
    await page.route('http://h.test/**', (r) => r.fulfill({ contentType: 'text/html', body: html(PAGE, SCRIPT) }));
    await page.goto('http://h.test/');
    await choose(page, 'Country', 'Canada', region(page, 'region', 'Editor'));
    expect(await page.locator('#inner').inputValue()).toBe('Canada');
    expect(await page.locator('#outer').inputValue()).toBe('Norway');
  });
  test('an option given as { name } is chosen by its name', async ({ page }) => {
    await page.route('http://h.test/**', (r) => r.fulfill({ contentType: 'text/html', body: html(PAGE, SCRIPT) }));
    await page.goto('http://h.test/');
    await choose(page, { name: 'Country' }, { name: 'Canada' }, region(page, 'region', 'Editor'));
    expect(await page.locator('#inner').inputValue()).toBe('Canada');
  });
  test('hover inside a scope hovers the text in that scope', async ({ page }) => {
    await page.route('http://h.test/**', (r) => r.fulfill({ contentType: 'text/html', body: html(PAGE, SCRIPT) }));
    await page.goto('http://h.test/');
    await hover(page, 'Invoice 1042', region(page, 'region', 'Editor'));
    await see(page, 'hovered inner');
  });
});

const { item, locate, doubleClick } = require('../e2e-support/outsider');
async function served(page, pages) {
  await page.route('http://n.test/**', (r) => r.fulfill({ contentType: 'text/html',
    body: pages[new URL(r.request().url()).pathname] || '<!doctype html><body>404</body>' }));
}
const SPA = (render) => '<!doctype html><body><button id="go">Open</button><main id="m"></main><script>' +
  "document.getElementById('go').onclick = () => { history.pushState({}, '', '/next'); " + render + ' };</script></body>';
const DETAILS = "document.getElementById('m').innerHTML = '<h1>Details</h1>'";

test.describe('v142 first look', () => {
  test.describe.configure({ timeout: 30000 });
  for (const [label, render] of [['in the next animation frame', `requestAnimationFrame(() => { ${DETAILS}; })`],
    ['after a 5 ms timer', `setTimeout(() => { ${DETAILS}; }, 5)`], ['inside the click', DETAILS]]) {
    test(`a page rendered ${label} is there at the first look`, async ({ page }) => {
      await served(page, { '/': SPA(render) });
      await page.goto('http://n.test/');
      await click(page, 'Open');
      await see(page, 'Details');
    });
  }
  test('a page rendered 250 ms after the click is still judged late', async ({ page }) => {
    await served(page, { '/': SPA(`setTimeout(() => { ${DETAILS}; }, 250)`) });
    await page.goto('http://n.test/');
    await click(page, 'Open');
    expect(await failure(see(page, 'Details'))).toMatch(/not on the page when it opened/);
  });
  for (const [label, body] of [
    ['a module bundle', '<div id="root"></div><script type="module">document.getElementById(\'root\').innerHTML = \'<h1>Loaded</h1>\';</script>'],
    ['a script that renders 300 ms after load', '<div id="root"></div><script>setTimeout(() => { document.getElementById(\'root\').innerHTML = \'<h1>Loaded</h1>\'; }, 300);</script>'],
  ]) {
    test(`a real <a href> to ${label} is a full load, not judged by first look`, async ({ page }) => {
      await served(page, { '/': '<!doctype html><body><a href="/full">Open full</a></body>', '/full': `<!doctype html><body>${body}</body>` });
      for (let i = 0; i < 3; i += 1) {
        await page.goto('http://n.test/');
        await click(page, 'Open full');
        await see(page, 'Loaded');
      }
    });
  }
  test('a form that posts to another page is a full load too', async ({ page }) => {
    await page.route('http://n.test/**', (r) => r.fulfill({ contentType: 'text/html', body: r.request().method() === 'POST'
      ? '<!doctype html><body><div id="root"></div><script>setTimeout(() => { document.getElementById(\'root\').textContent = \'Saved\'; }, 200);</script></body>'
      : '<!doctype html><body><form method="post" action="/done"><button>Save</button></form></body>' }));
    await page.goto('http://n.test/');
    await click(page, 'Save');
    await see(page, 'Saved');
  });
});

test.describe('v142 whole name first', () => {
  test.describe.configure({ timeout: 30000 });
  test('fill, choose, locate: the whole label wins over one that contains it; the containing one still works alone', async ({ page }) => {
    await page.setContent('<label>Username <input id="u"></label><label>Name <input id="n"></label>' +
      '<label>Confirm password <input id="c" type="password"></label><label>Password <input id="p" type="password"></label>' +
      '<label>Sort by status <select id="s1"><option>Old</option><option>Open</option></select></label>' +
      '<label>Status <select id="s2"><option>Old</option><option>Open</option></select></label>' +
      '<p>Amount due soon</p><label>Amount <input id="a" value="42"></label><label>Email address <input id="e"></label>');
    await fill(page, 'Name', 'Ada');
    await fill(page, 'Password', 'pw');
    await choose(page, 'Status', 'Open');
    expect(await page.evaluate(() => ['u', 'n', 'c', 'p', 's1', 's2'].map((id) => document.getElementById(id).value)))
      .toEqual(['', 'Ada', '', 'pw', 'Old', 'Open']);
    await expect(locate(page, 'Amount')).toHaveValue('42');
    await fill(page, 'Email', 'a@b.example');
    expect(await page.locator('#e').inputValue()).toBe('a@b.example');
    await choose(page, 'Sort', 'Open');
    expect(await page.locator('#s1').inputValue()).toBe('Open');
    await expect(locate(page, 'due soon')).toHaveText('Amount due soon');
  });
  test('a control containing the word wins over a plain text of the whole word; the text alone is still clicked', async ({ page }) => {
    await page.setContent('<h2 onclick="document.title=\'heading\'">Archive</h2><button onclick="document.title=\'button\'">Archive note</button>');
    await click(page, 'Archive');
    expect(await page.title()).toBe('button');
    await page.setContent('<h2 onclick="document.title=\'heading\'">Archive</h2>');
    await click(page, 'Archive');
    expect(await page.title()).toBe('heading');
  });
});

test.describe('v142 stated roles with more in their name', () => {
  test.describe.configure({ timeout: 30000 });
  const ROWS = '<table><tr><th>Invoice</th><th>Status</th><th></th></tr>' +
    '<tr><td>Invoice 1042</td><td>Paid</td><td><button onclick="document.title=\'1042\'">Open</button></td></tr>' +
    '<tr><td>Invoice 10420</td><td>Sent</td><td><button onclick="document.title=\'10420\'">Open</button></td></tr></table>';
  test('a row is the one whose cells contain the stated text (not a longer number)', async ({ page }) => {
    await page.setContent(ROWS + '<p id="m"></p><script>document.querySelectorAll("tr").forEach((r) => { r.ondblclick = () => { document.getElementById("m").textContent = "dbl " + r.cells[0].textContent; }; });</script>');
    await see(page, { role: 'row', name: 'Invoice 1042' });
    await doubleClick(page, { role: 'row', name: 'Invoice 1042' });
    await expect(page.locator('#m')).toHaveText('dbl Invoice 1042');
    await click(page, 'Open', locate(page, { role: 'row', name: 'Invoice 10420' }));
    expect(await page.title()).toBe('10420');
    expect(await failure(see(page, { role: 'row', name: 'Invoice 7' }))).toMatch(/nothing visible matches/);
  });
  test('two rows containing the text and none named by it: an error that names them', async ({ page }) => {
    await page.setContent('<table><tr><td>Invoice 1042</td><td>draft</td></tr><tr><td>Invoice 1042</td><td>final</td></tr></table>');
    expect(await failure(click(page, { role: 'row', name: 'Invoice 1042' }))).toMatch(/No row is named exactly "Invoice 1042" and 2 have a name containing it: \["Invoice 1042/);
  });
  test('tab / option / link / heading: the whole name first, else the one name containing it', async ({ page }) => {
    await page.setContent('<div role="tablist"><button role="tab" onclick="document.title=\'issues3\'">Issues 3</button></div>' +
      '<ul role="listbox" aria-label="Users"><li role="option" onclick="document.title=\'alice\'">alice-dev <small>Alice</small></li></ul>' +
      '<a href="#x" onclick="document.title=\'card\'"><strong>acme-docs</strong> <span>Public</span></a><h2>Reports <small>(3)</small></h2>');
    await click(page, { role: 'tab', name: 'Issues' });
    expect(await page.title()).toBe('issues3');
    await click(page, { role: 'option', name: 'alice-dev' });
    expect(await page.title()).toBe('alice');
    await click(page, { role: 'link', name: 'acme-docs' });
    expect(await page.title()).toBe('card');
    await see(page, { role: 'heading', name: 'Reports' });
    await seeOnce(page, { role: 'heading', name: 'Reports' });
    await page.setContent('<div role="tablist"><button role="tab" onclick="document.title=\'counted\'">Issues 3</button>' +
      '<button role="tab" onclick="document.title=\'whole\'">Issues</button></div>');
    await click(page, { role: 'tab', name: 'Issues' });
    expect(await page.title()).toBe('whole');
    await seeOnce(page, { role: 'tab', name: 'Issues' });
  });
  test('...but not a name running on, not two containing ones, not other roles', async ({ page }) => {
    await page.setContent('<a href="#f">acme-docs-fork</a><div role="tablist"><button role="tab">Open issues 3</button>' +
      '<button role="tab">Closed issues 1</button></div><button>Star 3</button>');
    expect(await failure(click(page, { role: 'link', name: 'acme-docs' }))).toMatch(/could not find anything to click: link named acme-docs\. Near: role link with a name containing "acme-docs": \["acme-docs-fork"\]/);
    expect(await failure(click(page, { role: 'tab', name: 'issues' }))).toMatch(/No tab is named exactly "issues" and 2 have a name containing it/);
    expect(await failure(see(page, { role: 'tab', name: 'issues' }))).toMatch(/No tab is named exactly "issues"/);
    expect(await failure(click(page, { role: 'button', name: 'Star' }))).toMatch(/button named Star\. Near: role button with a name containing "Star": \["Star 3"\]/);
  });
  test('notSee of a stated role: the whole name only (a row: the row containing it)', async ({ page }) => {
    await page.setContent('<a href="#d">Delete repository</a>' + ROWS);
    await notSee(page, { role: 'link', name: 'Delete' });
    expect(await failure(notSee(page, { role: 'row', name: 'Invoice 1042' }))).toMatch(/is no longer shown, but it is still visible/);
    await page.evaluate(() => document.querySelectorAll('tr')[1].remove());
    await notSee(page, { role: 'row', name: 'Invoice 1042' });
  });
  test('role words: capitalised and everyday names of a role; an unknown word is still a spec defect', async ({ page }) => {
    await page.setContent('<button onclick="document.title=\'saved\'">Save</button><label>Country <select id="c"><option>Canada</option><option>Peru</option></select></label>' +
      '<label>When <input id="d" type="date"></label>');
    await click(page, { role: 'Button', name: 'Save' });
    expect(await page.title()).toBe('saved');
    await choose(page, { role: 'select', name: 'Country' }, 'Peru');
    await choose(page, { role: 'dropdown', name: 'Country' }, 'Canada');
    expect(await page.locator('#c').inputValue()).toBe('Canada');
    await fill(page, { role: 'date', name: 'When' }, '2026-01-02');
    expect(await page.locator('#d').inputValue()).toBe('2026-01-02');
    expect(await failure(click(page, { role: 'btn', name: 'Save' }))).toMatch(/SPEC DEFECT: "btn" is no ARIA role/);
    expect(() => region(page, 'Dialog', 'Editor')).not.toThrow();
  });
});

test.describe('v142 item()', () => {
  test.describe.configure({ timeout: 30000 });
  test('visible entries only, no toast, the entry of the whole text; one entry used as the scope', async ({ page }) => {
    await page.setContent('<ul><li>acme-docs <button onclick="document.title=\'docs\'">Delete</button></li>' +
      '<li>acme-docs-fork <button onclick="document.title=\'fork\'">Delete</button></li></ul>' +
      '<table style="display:none"><tr><td>acme-docs</td></tr></table><div role="status" data-testid="toast">acme-docs deleted</div>' +
      '<div role="status"><ul><li>acme-docs-fork renamed</li></ul></div>');
    await click(page, 'Delete', item(page, 'acme-docs'));
    expect(await page.title()).toBe('docs');
    await click(page, 'Delete', item(page, 'acme-docs-fork'));
    expect(await page.title()).toBe('fork');
  });
  test('table cells joined without a space; a card known by data-testid; the innermost entry', async ({ page }) => {
    await page.setContent('<table><tr><td>Invoice 1042</td><td>42</td><td><button onclick="document.title=\'row\'">Pay</button></td></tr></table>' +
      '<div data-testid="card"><h3 data-testid="title">Order 7</h3><span>Unpaid</span><button onclick="document.title=\'card\'">Pay</button></div>' +
      '<ul><li>Group A: <ul><li>Note 5<button onclick="document.title=\'inner\'">Open</button></li></ul></li></ul>');
    await click(page, 'Pay', item(page, 'Invoice 1042'));
    expect(await page.title()).toBe('row');
    await click(page, 'Pay', item(page, 'Order 7'));
    expect(await page.title()).toBe('card');
    await see(page, 'Unpaid', item(page, 'Order 7'));
    await click(page, 'Open', item(page, 'Note 5'));
    expect(await page.title()).toBe('inner');
  });
  test('no entry, or two entries showing the text: an error, not a guess', async ({ page }) => {
    await page.setContent('<ul><li>Invoice 1042 <button>Pay</button></li><li>Invoice 1042 <button>Pay</button></li></ul>' +
      '<div role="grid"><div role="row"><span role="gridcell" aria-label="A1">1</span></div></div>');
    expect(await failure(click(page, 'Pay', item(page, 'Invoice 1042')))).toMatch(/is not ONE entry: 2 visible entries show "Invoice 1042"/);
    expect(await failure(see(page, 'Paid', item(page, 'Invoice 7')))).toMatch(/No visible list entry, card or row shows "Invoice 7"/);
    expect(await failure(see(page, 'x', item(page, 'A1')))).toBe('SPEC DEFECT: "A1" is a grid cell, not a list item: use region(page, "gridcell", "A1")');
    await expect(item(page, 'Invoice 7')).toHaveCount(0);
  });
});

test.describe('v142 notSee, see, hover, choose', () => {
  test.describe.configure({ timeout: 30000 });
  test('notSee in a scope that is not there fails; in a scope that is there it checks', async ({ page }) => {
    await page.setContent('<ul><li>Invoice 1042 Unpaid</li></ul><section aria-label="Editor"><p>Draft</p></section>');
    expect(await failure(notSee(page, 'Invoice 1042', item(page, 'No such row')))).toMatch(/^SPEC DEFECT: notSee\(\) looks inside item\(page, "No such row"\)/);
    expect(await failure(notSee(page, 'Invoice 1042', region(page, 'dialog', 'Editor')))).toMatch(/^SPEC DEFECT: notSee\(\) looks inside region\(page, "dialog", "Editor"\)/);
    await notSee(page, 'Overdue', item(page, 'Invoice 1042'));
    await notSee(page, 'Invoice 1042', region(page, 'region', 'Editor'));
    expect(await failure(notSee(page, 'Draft', region(page, 'region', 'Editor')))).toMatch(/still visible/);
  });
  test('see: past eight hidden copies; a selected option is shown, one not selected is not', async ({ page }) => {
    await page.setContent('<span style="display:none">acme-docs</span>'.repeat(10) + '<span>acme-docs</span>' +
      '<select><option>Canada</option><option selected>Peru</option></select>');
    await see(page, 'acme-docs');
    await see(page, 'Peru');
    expect(await failure(see(page, 'Canada'))).toMatch(/nothing visible matches/);
  });
  test('hover: an icon button by its aria-label; a stated role counts', async ({ page }) => {
    await page.setContent('<button aria-label="Info" title="More info" onmouseenter="document.title=\'info\'">i</button>' +
      '<p onmouseenter="document.title=\'text\'">Info panel</p>');
    await hover(page, { role: 'button', name: 'Info' });
    expect(await page.title()).toBe('info');
    await hover(page, 'Info panel');
    expect(await page.title()).toBe('text');
    await hover(page, 'Info');
    expect(await page.title()).toBe('info');
    expect(await failure(hover(page, { role: 'link', name: 'Info' }))).toMatch(/Nothing on the page shows link named Info/);
  });
  test('choose on a native select: the option containing the words; a missing option fails fast and names the options', async ({ page }) => {
    await page.setContent('<label>Country <select id="c"><option>Norway (NO)</option><option>Canada (CA)</option></select></label>');
    await choose(page, 'Country', 'Canada');
    expect(await page.locator('#c').inputValue()).toBe('Canada (CA)');
    const begun = Date.now();
    expect(await failure(choose(page, 'Country', 'Peru'))).toMatch(/The dropdown Country has no option Peru; its options: \["Norway \(NO\)","Canada \(CA\)"\]/);
    expect(Date.now() - begun).toBeLessThan(6000);
  });
});

const GRID_CELLS = '<div role="grid" aria-label="Sheet"><div role="row">' +
  '<div role="gridcell" aria-label="A1" tabindex="0" style="display:inline-block;width:80px;height:24px">old</div>' +
  '<div role="gridcell" aria-label="B1" tabindex="0" style="display:inline-block;width:80px;height:24px"></div></div></div>' +
  '<p id="m">none</p>';
const COMMIT = "function commit(cell, value, how) { cell.textContent = value; document.getElementById('m').textContent = " +
  "'committed ' + cell.getAttribute('aria-label') + '=' + value + ' via ' + how; cell.focus(); }" +
  "function cancel(cell, old) { cell.textContent = old; document.getElementById('m').textContent = 'cancelled'; cell.focus(); }";
const EDITOR = (mode) => '<script>' + COMMIT + "let sel = null, old = '';" +
  "document.querySelectorAll('[role=gridcell]').forEach((c) => { c.onclick = () => { sel = c; c.focus(); }; });" +
  "document.addEventListener('keydown', (e) => {" +
  "  if (!sel || e.target.tagName === 'INPUT' || e.key.length !== 1 || e.ctrlKey || e.metaKey) return;" +
  "  const cell = sel; old = cell.textContent;" +
  "  const mount = (value) => { const input = document.createElement('input'); input.setAttribute('aria-label', 'Cell editor');" +
  "    input.value = value; cell.textContent = ''; cell.appendChild(input); input.focus();" +
  "    input.onkeydown = (k) => { if (k.key === 'Enter' || k.key === 'Tab') { k.preventDefault(); commit(cell, input.value, k.key); }" +
  "      if (k.key === 'Escape') cancel(cell, old); }; };" +
  (mode === 'seed' ? "  e.preventDefault(); const first = e.key; Promise.resolve().then(() => mount(first));"
    : "  mount('');") +
  "});</script>";
const DRAFT_CELL = '<script>' + COMMIT + "let sel = null, draft = null, old = '';" +
  "document.querySelectorAll('[role=gridcell]').forEach((c) => { c.onclick = () => { sel = c; c.focus(); };" +
  "  c.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); if (draft !== null) commit(c, draft, 'Enter'); draft = null; return; }" +
  "    if (e.key === 'Escape') { if (draft !== null) cancel(c, old); draft = null; return; }" +
  "    if (e.key.length === 1) { e.preventDefault(); if (draft === null) { old = c.textContent; draft = ''; } draft += e.key; c.textContent = draft; } }; });" +
  '</script>';

test.describe('v142 typing on a grid cell', () => {
  const { typeText } = require('../e2e-support/outsider');
  for (const mode of ['focus', 'seed']) {
    test(`typeText on a selected cell whose first key opens an editor (${mode}); Enter commits`, async ({ page }) => {
      await page.setContent(GRID_CELLS + EDITOR(mode));
      await click(page, { role: 'gridcell', name: 'B1' });
      await typeText(page, '=A1*2');
      await press(page, 'Enter');
      await see(page, 'committed B1==A1*2 via Enter');
      await expect(page.getByRole('gridcell', { name: 'B1' })).toHaveText('=A1*2');
    });
  }
  test('typeText then Tab commits and Escape cancels, each its own press()', async ({ page }) => {
    await page.setContent(GRID_CELLS + EDITOR('seed'));
    await click(page, { role: 'gridcell', name: 'B1' });
    await typeText(page, '42');
    await press(page, 'Tab');
    await see(page, 'committed B1=42 via Tab');
    await click(page, { role: 'gridcell', name: 'A1' });
    await typeText(page, 'new text');
    await press(page, 'Escape');
    await see(page, 'cancelled');
    await expect(page.getByRole('gridcell', { name: 'A1' })).toHaveText('old');
  });
  test('typeText on a cell that keeps its own draft (no editor input)', async ({ page }) => {
    await page.setContent(GRID_CELLS + DRAFT_CELL);
    await click(page, { role: 'gridcell', name: 'A1' });
    await typeText(page, 'Q3 total');
    await press(page, 'Enter');
    await see(page, 'committed A1=Q3 total via Enter');
  });
  test('typeText types into a focused text field as well (appending, like a person)', async ({ page }) => {
    await page.setContent('<label>Name <input id="n" value="Ada"></label>');
    await click(page, { role: 'textbox', name: 'Name' });
    await page.locator('#n').press('End');
    await typeText(page, ' Lovelace');
    expect(await page.locator('#n').inputValue()).toBe('Ada Lovelace');
  });
  test('typeText refuses what it cannot type, before any key reaches the page', async ({ page }) => {
    await page.setContent('<label>Name <input id="n"></label>');
    await click(page, { role: 'textbox', name: 'Name' });
    expect(await failure(typeText(page, ''))).toMatch(/^SPEC DEFECT: typeText\(page, text\) .* empty text/);
    expect(await failure(typeText(page, '42\n'))).toMatch(/^SPEC DEFECT: typeText.*Enter, Tab and Escape are keys of their own/);
    expect(await failure(typeText(page, '42\t'))).toMatch(/^SPEC DEFECT: typeText/);
    expect(await failure(typeText(page, { role: 'gridcell', name: 'A1' }))).toMatch(/^SPEC DEFECT: typeText.*takes no target/);
    expect(await page.locator('#n').inputValue()).toBe('');
  });
  const CELLS_AND_FIELD = GRID_CELLS + '<table role="grid" aria-label="T"><tr><td aria-label="C1">x</td></tr></table>' +
    '<label>Formula bar <input id="f"></label>';
  test('fill() on a gridcell is a SPEC DEFECT that names typeText; on a real field it types', async ({ page }) => {
    await page.setContent(CELLS_AND_FIELD);
    expect(await failure(fill(page, { role: 'gridcell', name: 'B1' }, '5'))).toMatch(/^SPEC DEFECT: fill\(\) types into a text field.*typeText\(page/);
    await fill(page, 'Formula bar', '=A1');
    expect(await page.locator('#f').inputValue()).toBe('=A1');
  });
  test('fill() on a td of a grid is a SPEC DEFECT too', async ({ page }) => {
    await page.setContent(CELLS_AND_FIELD);
    expect(await failure(fill(page, 'C1', '5'))).toMatch(/^SPEC DEFECT: fill\(\) types into a text field/);
  });
  test('press() of text is a SPEC DEFECT that names typeText', async ({ page }) => {
    await page.setContent(GRID_CELLS);
    expect(await failure(press(page, '=A1+B1'))).toMatch(/^SPEC DEFECT: press\(\) takes a key.*fill\(\)\/typeText\(\) -- got "=A1\+B1"; .*typeText\(page, text\)/);
  });
});

test.describe('v142 options: native select and aria-selected', () => {
  test('an option of a visible native select is shown, and clicking it selects it', async ({ page }) => {
    await page.setContent('<label>Role <select id="r"><option>Member</option><option>Owner</option></select></label>');
    await see(page, { role: 'option', name: 'Member' });
    await click(page, { role: 'option', name: 'Owner' });
    expect(await page.locator('#r').inputValue()).toBe('Owner');
  });
  test('an option the native select does not hold is not shown', async ({ page }) => {
    await page.setContent('<label>Role <select id="r"><option>Member</option><option>Owner</option></select></label>');
    expect(await failure(see(page, { role: 'option', name: 'Admin' }))).toMatch(/option named Admin is shown, but nothing visible matches/);
  });
  test('a click on an option names the whole option of a native select', async ({ page }) => {
    await page.setContent('<label>Role <select id="r"><option>Member</option><option>Owner</option></select></label>');
    expect(await failure(click(page, { role: 'option', name: 'Own' }))).toMatch(/could not find anything to click: option named Own/);
    expect(await page.locator('#r').inputValue()).toBe('Member');
  });
  test('a custom listbox option is still clicked as an element', async ({ page }) => {
    await page.setContent('<ul role="listbox" aria-label="Team"><li role="option" aria-selected="false" ' +
      'onclick="this.setAttribute(\'aria-selected\', \'true\'); document.title = \'picked\'">frontend-team</li></ul>' +
      '<label>Other <select><option>frontend-team</option></select></label>');
    await click(page, { role: 'option', name: 'frontend-team' });
    expect(await page.title()).toBe('picked');
  });
  test('check() of an option reads aria-selected and clicks only when the state differs', async ({ page }) => {
    await page.setContent('<ul role="listbox" aria-label="Labels" aria-multiselectable="true">' +
      ['bug', 'docs'].map((n) => `<li role="option" aria-selected="${n === 'docs'}" data-n="0" ` +
        'onclick="this.dataset.n = String(Number(this.dataset.n) + 1); this.setAttribute(\'aria-selected\', ' +
        'String(this.getAttribute(\'aria-selected\') !== \'true\'))">' + n + '</li>').join('') + '</ul>');
    await check(page, { role: 'option', name: 'bug' });
    await check(page, { role: 'option', name: 'docs' });
    await check(page, { role: 'option', name: 'docs' }, false);
    const state = await page.locator('li').evaluateAll((els) => els.map((e) => e.textContent + ':' + e.getAttribute('aria-selected') + ':' + e.dataset.n));
    expect(state).toEqual(['bug:true:1', 'docs:false:1']);
  });
});
