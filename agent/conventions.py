PROJECT_CONVENTIONS = """\
Project layout (already scaffolded; keep it, extend it):
- frontend/: React 19 + Vite 7 + Tailwind 4, TypeScript/TSX.
  - frontend/src/main.tsx (read-only) wraps <App /> in <BrowserRouter>, set to render a navigation at once; frontend/src/App.tsx holds <Routes> and any context providers.
  - Home page ("/") is frontend/src/pages/HomePage.tsx. Add pages in frontend/src/pages, components in frontend/src/components.
  - API client: frontend/src/api/index.ts (axios instance, baseURL "/api").
- frontend tests: frontend/tests/**/*.test.tsx, run by Vitest (jsdom, globals) with @testing-library/react,
  @testing-library/user-event and @testing-library/jest-dom (matchers preloaded by frontend/test/setup.ts).
  Render the app with: render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>) (import App from "../src/App").
- backend/: Express 5, CommonJS. backend/src/app.js registers /api/* routes (add them before the static/SPA block).
  SQLite helpers live in backend/src/database (init_db.js: CREATE TABLE IF NOT EXISTS; db_runtime.js: run/get/all).
  db_runtime.js requires init_db.js, so init_db.js must NEVER require db_runtime.js (circular import: `run` would be
  undefined). Inside init_db.js create tables and seed rows with the raw sqlite handle it already owns; everywhere
  else use run/get/all from db_runtime.js. Do not rewrite db_runtime.js or index.js.
- backend tests: backend/tests/**/*.test.js, Vitest + supertest against `require("../src/app")`.
- E2E tests: backend/test-e2e/*.spec.js using @playwright/test; baseURL is preconfigured, use page.goto("/").
- Small files: keep every source file under about 12000 characters. A page grows by new components in
  frontend/src/components (a menu, a dialog, a panel each in its own file), a route file by new files in backend/src/routes,
  the seed by one seed file per area required from init_db.js. One answer of the implementer holds about 45000
  characters, its thinking included: a large existing file is changed by targeted edits, never re-sent whole, and a
  file of more than about 800 lines is split -- a self-contained part (a component, a hook, a route group, a helper
  module) moved into a file of its own and imported -- before anything is added to it (hackathon sheet: a 933-line grid
  component re-sent whole in repair after repair was cut off by the output limit six times and its leaf was lost; sheet
  v103: EditorPage.tsx 27209 and routes/workbooks.js 26527 characters could not be shown whole). ADR-099 section 48.
- Production: the backend serves the built frontend (frontend/dist) on one port. Never hardcode ports or hosts.
- A server error is never silent: every `catch` that answers with a 5xx status first does `console.error(err)`, and
  app.js ends with an error-handling middleware `(err, req, res, next)` that logs `err` and answers 500 JSON. The
  server's output is shown to you when a check fails; a swallowed error leaves you guessing.
- Persistence: SQLite goes through backend/src/database (its file path already honours env ARC_DB_FILE). Any other
  persistent file (JSON stores, sessions) must live under `process.env.DATA_DIR || path.join(__dirname, "..", "data")`,
  resolved at call time. The harness starts every test run and every throw-away server with a fresh DATA_DIR/ARC_DB_FILE.
- Pre-existing data: whenever the requirement says the system "contains" / "has" a record (an account with a given
  username and password, an order, a passenger, 系统中已存在...), that record must exist after a FRESH start with an
  empty data directory: seed it idempotently during database initialisation (INSERT OR IGNORE / upsert), hashing
  passwords exactly like the registration code does. The seed is written once, at start-up, into an empty database;
  while the application runs it never restores, resets or re-seeds data (not on a sign-in, not on a page load) --
  what users changed stays changed: after a password change the old password fails, and another browser session sees
  the same data. Behaviour the requirement itself describes (an automatic status change) still happens. Seeds
  accumulate across requirements: add to the seed routine,
  never replace what earlier requirements seeded (no record or field is dropped; the one exception is a grant a
    later requirement states for the same subject and resource: that updates the grant, see "Seed rows that grant"
  below; and a second copy of one seed record that a finding names and that you have confirmed to have the same
  kind, name and owner or parent may be removed, nothing else). A record is seeded in exactly the STATE the requirement gives it
  ("an active note", "not pinned", "without the X label", "an unread message"). When a scenario's GIVEN describes a later
  VIEW the user is on ("the user is viewing the archived list") and the application offers the user a way to get
  there, the scenario gets there through the user's own steps; the seed never skips them -- other flows start from the
  stated state. A record state the GIVEN or the requirement's seed sentences state as ALREADY true ("a Closed issue",
  "a Draft PR", "one approving review", "a merged branch") is seeded in that state: the test does not bring it about
  first. The same holds for an OUTCOME: "a deletable note X" that a scenario ends up showing in the trash is
  seeded NOT deleted -- the user (or the test) deletes it first; seeded already in the trash, it is missing from the
  page every other flow starts on (ADR-097). A state no user of this application can bring about (time has passed, an outside system or an
  administrator acted) is seeded as it is stated.
  When scenarios give the SAME record contradictory starting states ("a fresh worksheet whose A1 is 2" in one, "whose
  A1 is Saved value" in another) and the requirement does not NAME that record, nothing is seeded for them: each scenario
  creates its own record through the interface and enters its starting values there. A record the requirement NAMES as
  seeded ("the evaluation seed contains the seeded workbook `Q3 Sales`", "Seed values: account `alice-dev` ...") is
  always seeded under exactly that name, with the UNION of the contents its scenarios state, cell by cell; only where
  two statements give the same cell different values does the one stated by more scenarios win (ADR-099 section 71).
  A scenario that states other
  contents for it creates its own record through the interface and enters them there -- it never edits the shared
  seed, which every later test of the same run still reads (ADR-099 sections 47-48).
  A named seed record is ONE record per place the requirement puts it: one set of the contents the requirement states
  for it, never spread over several records of that name (a search then lands on a copy without them). Only when the
  requirement itself places the name under two owners (an organization's repository and a user's) are there two
  records -- each with what the requirement states for THAT owner's record (audit r33). A record the requirement
  places under an owner (an organization's repository, a team's grant) is owned by THAT owner in the data model, so
  every rule that reads ownership (membership, removing a member's access) finds it there -- not by the account that
  created it in the seed. When the requirement says the seed supplies SEPARATE records for different outcomes (one that
  succeeds and one that is refused, one eligible and one blocked), each is seeded already in the state its scenario
  checks: the eligible one meets every condition the requirement lists for success; the other lacks exactly what the
  requirement names for the refusal (ADR-099 section 86).
  A GIVEN that puts a role at the controls ("an organization Owner is signed in", "a repository Admin") is performed with an account the requirement gives credentials for; when it gives credentials for only one
  account and says nothing else about that account's role, that account holds the role in the seed -- seeded as a
  mere member, none of those scenarios can start. Where the requirement DOES state an account's role, that statement
  wins, and a scenario that needs another role uses another account (audit r33) (ADR-099 section 66).
- Outcomes and confirmations: the result of an action the requirement describes ("displays Approved", "shows the
  saved description") appears in the view where the action was taken, without switching tabs or pages the requirement
  does not mention. A confirmation contains exactly the steps and fields the requirement lists -- no extra required
  input of your own (a local github run demanded the repository name be retyped where the requirement says it need not
  be) -- and an action that is refused shows why; it never just closes (ADR-099 section 70). A create or submit the
  server rejects shows the server's reason and stays on its page with the input kept; it never navigates to another,
  already existing record, which would make the refused attempt look successful.
- State the requirement says survives a reload or a switch (of worksheet, tab, page) -- a view, filter, setting or
  selection the user creates -- is saved to the server the moment it is created or changed, not only when it is later
  applied or submitted, and every view reads it back from there; a flag kept only in the component is lost on the switch.
- Sign-in: lock behind sign-in only what the requirement ties to a signed-in user (a scenario whose GIVEN says the
  user is logged in / authenticated, or the current user's own "My ..." data). Everything else -- reading, creating,
  editing, the actions offered on a content page -- works signed out as well. When one requirement shows something
  after login and another shows the same thing with no word about login, it is shown in both states. The lock is per
  SECTION, never per page: a home page with "My ..." sections and a "Recently Updated Pages" list shows that list to
  everyone (hiding the whole dashboard from a signed-out visitor hid the list). An action a scenario performs with no
  sign-in step (such as Favorite on a content page) works signed out too: keep a signed-out visitor's choices
  per browser (an anonymous visitor behind a cookie), and the "My ..." lists show that visitor's. This is only for
  actions nothing restricts: when the requirement (the description, an ancestor, the scenario) reserves an action for
  signed-in users, a role or an owner, that restriction holds -- a scenario that merely omits its sign-in step does
  not open the action to visitors. A control that a
  scenario's GIVEN places on a page ("the user is on the page edit page") is on THAT page. (ADR-099)
- An example name built from a seeded value ("each row is named like `Item Blue shirt`" where "Blue shirt" is a seeded
  item) shows the pattern with that value as the variable part: `Item <the item's full name>`. The row of "Red" is
  `Item Red` -- never `Item Red shirt`: the words of the example that belong to the seeded value are not a fixed
  suffix, and two rows must not end up with the same name. This is only about names built from a seeded value. When
  the requirement states the format itself ("every row is named `Item <name> (editable)`"), that format is kept.
- Test data, in this order: (1) data the requirement says already exists is used verbatim, never re-created;
  (2) otherwise a test creates its own uniquely named records (Date.now() + random suffix) and never assumes an empty
  store or a test order; (3) a destructive step touches only the record the requirement dedicates to that scenario, or
  a record the test created itself.
- Interface language = the language of the interface texts the requirement itself quotes. Every visible text uses the
  requirement's own terms; a reference screenshot in another language shows structure only, none of its texts are copied.
- THE REQUIREMENT OUTRANKS EVERYTHING BELOW. Whatever the requirement states about an element is built exactly so:
  its role ("the button named X", "a heading", "a textbox", "a dialog named Y", "a region", "a form with role form",
  "an article", "a status message"), its accessible name (verbatim: visible text, <label>, or aria-label /
  aria-labelledby for regions, dialogs, forms and icon-only controls), and its number ("exactly one", "unique": no
  second visible element with that role and name WHERE THE REQUIREMENT COUNTS -- inside the container the sentence
  names ("each label row contains a unique `Save` button" = one Save per row, and every row has its own; "the unique
  button `Reports` in the sidebar" = one inside the sidebar), and on the whole page only when the sentence names no
  container: then not in a header, a sidebar, a hidden-but-rendered menu or a footer either). The conventions after
  this line are defaults for what the requirement leaves open; when
  one of them disagrees with the requirement's own words, the requirement wins. Reference screenshots show layout and
  structure only and never outrank the requirement's text either.
- Never invent a name for a dialog, region or form. When the requirement has NAMED the dialog in which an item is
  worked on (say, "a dialog named `Order editor`" with a `Close` button) and another requirement speaks of an unnamed
  editor or panel for the same item ("the tag editor exposes a checkbox ... and a `Close` button"), that editor is --
  unless the requirement states another container for it, named or not ("opens a separate dialog") -- part of the SAME named dialog: open that dialog and show
  the panel inside it. A second dialog with a name of your
  own (or one read off a screenshot) is an element the requirement never mentions. A phrase that DESCRIBES a panel ("the
  tag editor", "the settings panel") is not a name: it gives the panel no dialog, and no aria-label, of its own.
- "X exposes Y" / "X has Y" / "X contains Y", where X is a container (a page, the sidebar, a dialog, an editor, a card,
  an article, a row): Y is an element the container shows INSIDE it -- a button, a heading, a checkbox, a message, or an
  element carrying the stated title / tooltip ("the shipped order exposes title `Shipped`" -> inside the order's element,
  the status indicator carries title="Shipped"). This is about things SHOWN in a part of the page. When Y is X's own
  role, name or state ("the form is exposed as role form named `X`", "the button exposes aria-expanded=true"), Y belongs
  to X itself; when Y is a page ("the flight exposes a booking page"), X leads to that page. A title or tooltip
  attribute stated for a CONTAINER goes in both places -- on the container AND on a visible element inside it carrying
  the same attribute -- even when the requirement seems to give it to the container alone ("the pinned article exposes
  a title attribute `Pinned`"): a locator scoped to the container looks INSIDE it, so an attribute that sits only on
  the container itself is out of reach. Put it on ONE element the container already shows (its heading, its status
  marker, its text) -- exactly one inside: two inside (the heading AND the button in it) make that scoped locator find
  two and stop (keep local v99, REQ-2.8.1/2.8.3) -- never add an empty element to carry it, and never for an element that cannot have children
  (an <input>, an <img>): there the attribute stays on the element itself.
- Accessibility contract: buttons/links must expose the exact accessible names given in the requirement;
  elements the requirement names by "test id" must carry that value in a data-testid attribute. Exact means nothing
  more: a count, a number, an id or a second phrase shown with a named control or with a heading that the requirement
  defines as a record's title sits in its own element OUTSIDE that control / heading (a filter link "Open" with a
  count beside it, not "Open 2"; a title heading "Fix search" with "#2" next to it, not inside it).
- Roles are binding, not styling: when the requirement says 链接 / link, render a real <a href> or router <Link>
  (ARIA role "link"), even for an action (use <a href="/" onClick=...>); when it says 按钮 / button, render a <button>.
  Never implement a link as a "link-styled button" or the reverse. When the requirement leaves the kind open ("a link
  or button named X", "the X entry"), the kind is decided in this order: (1) the kind this requirement names for that
  control, also in another sentence that means the same control (same page, container and purpose), not merely the
  same word; (2) the kind the control already has when the existing application (an earlier stage) renders it;
  (3) these defaults: a pure navigation entry -- its only effect is to open another page: the entries that take a
  signed-out visitor to the sign-in and sign-up pages, the shared header's navigation entries, the entries of a
  navigation bar (a repository's or an organization's sections such as Code, Issues, Pull requests, Settings; a
  record's tabs that open their own view), a record shown on a page that opens that record's page -- is a link
  (<Link to=...>), as on the real product; any other control is ONE <button type="button"> -- and when X is a list
  item's own title that opens the item in place, that button nested in the item's heading, as the rule on repeated
  items below says. The sign-in FORM's submit control stays a <button>
  (a requirement typically names both: the “Sign in” link that opens the page and the “Sign in” button that submits). Entries the requirement calls options of a picker, selector or list are each an element
  with role="option" inside role="listbox" (or the <option>s of a native <select>), also when they are built from data;
  their chosen state is aria-selected -- never one button or checkbox per entry.
- Two names for one control are rendered once: when the requirement offers two NAMES FOR THE SAME control (typically
  the same word in two languages: `登录` 或 `Login`), render exactly ONE element carrying the name that is in the interface
  language. Two different things the user chooses between (`Grid` or `List` view, `Email` or `Phone` login) are two controls. This rule is about alternative names only, never
  about repeated words: two DIFFERENT controls in DIFFERENT parts of the page may carry the same word (a `登录` link in
  the header and the `登录` submit button of the login form) and both stay. Never remove, hide or rename a control the
  requirement names in order to avoid a repeated name. Inside ONE container, however -- a dialog, a form, an article,
  a menu -- a name is one control unless the requirement asks for several: when it says the container has "a button named `Close`", a panel shown
  inside it uses that Close, it does not bring a second one of its own.
- Unless the requirement asks for the browser's own validation, validation lives in the application: every <form> carries `noValidate`, and every rule (required,
  format, length, match) is checked in the submit handler, which shows the requirement's own message. Browser-native
  validation (the bubble of `required` / `type="email"` / `pattern`) blocks the submit handler, is not part of the page
  and is not in the requirement's language. Keep `type="email"` etc. for semantics only.
- Repeated items (cards, rows, list entries): each item is ONE container element -- of the role the
  requirement gives the item (article, row, listitem ...); when it gives none: <li> / role="listitem" -- that holds the item's title text AND its own action buttons, so that a user or a locator can first pick the item by
  its text and then the action inside it. Every action button has a text or aria-label name taken from the requirement.
  An item the user can open carries its title in ONE text node that is BOTH a heading and the control:
  `<li><h3><button type="button">Book 5</button></h3></li>` -- the control nested INSIDE the heading, a link
  (`<h3><a href="...">`) where the order of kinds above gives a link (the requirement says link, or the item opens a
  page of its own). The REST of such an item opens it too: a person clicks
  the card, not only its title -- give the container the same click handler, and have that handler IGNORE a click
  that lands on any interactive element INSIDE the item: `const hit = (event.target as HTMLElement).closest('button,
  a, input, select, textarea, label, [role]'); if (hit && hit !== event.currentTarget &&
  (event.currentTarget as HTMLElement).contains(hit)) return;` -- a checkbox, a link or a menu in the card must do
  only its own thing (closest() also matches the container itself and every ancestor with a role, e.g. a
  role="listitem" card or a role="list" parent: without the contains() test every click on the card is ignored). No role or name is added to the container (ADR-099, audit r22) -- unless the requirement gives the
  item a role or an accessible name (e.g. "each row is named with its member"): then it carries exactly that. The item's action buttons sit in ONE
  compact row along its bottom edge (icon buttons with aria-labels, never wrapping into several rows): the middle of
  the item is its title and content, never an action. A grid row stretches a card to its tallest neighbour, so the
  card is a column -- `<article className="flex min-h-28 flex-col">`, title and content in a `flex-1` body, the action
  row LAST with `mt-auto` -- and it is at least twice as tall as its action row even with a title alone: its middle
  stays on the body whatever its height (keep local v88: a delete re-flowed the grid and the click that should open
  "Project ideas" pressed "Pin note"; ADR-099).
  Never write the title twice as two elements
  (`<h3>Book 5</h3><button>Book 5</button>`): two copies of the words are two visible matches for the same name, and
  a spec that says "exactly one Book 5" then fails on a correct page. Exceptions, in this order: an item the
  requirement gives no action for (nothing to open, no details page) stays a heading with no control; and an item
  whose own container already has an interactive role (menuitem, option, tab, row that is itself the control) carries
  the title as that container's name, without a second control inside. The heading is what makes the item's own title
  findable; the rule below about naming an opener after what it opens is for generic openers ("Settings", "More
  options"), never for an item that carries its own title.
- Names come from the requirement's own words, never from a synonym: an icon-only control is labelled with the
  requirement's phrase for it (the "list-view icon" -> aria-label "List view", not "Toggle view"); a notification
  repeats the requirement's verb for the action (delete -> "... deleted", archive -> "... archived"), and its message
  is an element of its own beside its action button -- `<div role="status"><span>{message}</span><button>Undo</button></div>`,
  never the bare message text in the same element as the button (its text would be "Note trashedUndo"; ADR-099).
- A control that opens a menu or dialog carries the name the requirement gives it; only when the requirement gives
  none is it named after what it opens ("Settings menu", "More options"), never after an item inside it.
- A field the requirement tells the user to type into really accepts typing: never a read-only or disabled <input>.
  An edit in progress lives in component STATE (a controlled input), never only in the DOM: a re-render that writes
  the stored value back into the element erases what the user typed. Any action that changes what is being edited --
  selecting another cell, row or item, opening another record, navigating -- first commits the pending edit (in that
  action's own handler, before it changes the selection), so the user never loses typing by clicking elsewhere.
- What a person expects from ordinary UI, unless the requirement says otherwise: a dialog form submits on Enter and
  closes on Escape; navigation between named views (tabs, sub-pages) renders THAT view's content, never only a URL
  hash change; a control or page the requirement reserves for a role is absent for other users AND its route/API
  refuses them (server-side too); a record a visitor may not see is refused on EVERY page and API under it (its
  sub-pages, tabs, settings, history), exactly as on its main page; a link in a list opens exactly the record that
  item shows (by its owner and id), never another record that shares its name; after a successful write, every visible value it changes (lists, counts, "last
  updated", names) shows the new value without a reload; times are shown readable, not as raw ISO strings; the
  document title names the page or record; minimal styling still styles headings as headings (a page's h1 visibly
  larger or bolder than body text -- the Tailwind reset leaves it at body size otherwise), unless the requirement
  describes another look. Seed rows that grant a role or permission state it exactly: a later seed
  line for the same pair updates it (upsert), never an "insert or ignore" that keeps an earlier, weaker grant.
- The SERVER enforces what the requirement states, not only the page: each operation checks ITS OWN stated list of
  allowed roles -- roles are not a ladder unless the requirement says so (a role stated for viewing or triage does
  not get create or edit rights because a "higher" role has them; a role stated for editing does not change status
  unless stated); every stated limit (length, format, uniqueness, "only one open X per Y", who a grant may go to) is
  checked by the API too and refused with the stated message; a record a viewer may not open is also left out of
  every list, count, search result and list API that viewer gets. A create is never doubled by a double click: the
  submit button is disabled while its request runs, and numbers are assigned atomically on the server.
- Build only the rules the requirement states: no invented restrictions or side effects (nothing is protected,
  locked or changed as a by-product unless stated). A control sends exactly what the user chose (a choice dialog
  sends the chosen option; an opener only opens, it never submits). Every field the requirement lists for a row,
  card or detail page is shown there, also when listed in plain words without quotes. Long values wrap or end in an
  ellipsis inside their box; no text, menu or popover runs past the edge of the window.
- Context survives navigation: a breadcrumb, a back link or a new search keeps the scope it was opened in (the
  current branch, record, filter); an item's history link shows that item's history. Identifiers that contain "/"
  are URL-encoded in routes and API paths. Clicking an already-selected filter keeps it selected; clearing a query
  and searching again shows unfiltered results, never the previous ones. "Your ..." lists show only the current
  user's items. Re-entering a multi-step flow starts at its first step. A value the requirement compares character
  for character is not trimmed or normalised. An unknown address or unknown record shows a visible not-found
  message -- never a blank page, never a working-looking page with forms. Two links or options on one page never
  share an accessible name unless they open the same thing (show the owner when two records share a name).
- A view the user switched to (a tab, a filter, a sub-page of a record) is addressable: the URL reflects it and a
  reload shows the same view, not the default one. The result of an action appears where the action was taken (a
  submitted review shows on that page's conversation), besides any other view the requirement names. A control
  or text named only in a scenario's steps is as required as one in the description. Content the requirement says
  a page shows (a diff, a list, a history) is visible when the page opens, not collapsed behind a toggle. A state
  label next to a name ("Private", "Draft", "Open") is its own element, never glued into the name's text. Every value
  a THEN says is displayed (a name, an author, a title, a path) is the whole text of its own element: an author shown
  with a time is `<span>author</span> committed <time>...</time>`, not one run of text. A file the requirement says
  the system pre-provisions or a repository contains, named by a bare file name (no folder given for it or its
  context), is seeded at the repository root under exactly that path -- a file the user creates, uploads or renames in
  a scenario is not seeded. A search result, list entry or link for a file shows the path as the requirement writes it
  as its own text (where two such entries share a page, an owner or repository prefix sits in its own element beside
  it).
- Data stays whole: an operation that combines or copies records (a merge, a fork, an import) never deletes or
  overwrites what the target already holds beyond what the requirement says it changes; a derived value (a count,
  a latest item, an "updated" time) follows its source on every change -- "updated" is the last change, not the
  creation; a default state the requirement gives a record (a default role, status, flag) is stored with the record,
  not only shown by the page; a permission granted through a group counts exactly like a direct one; relations are
  shown in the stated direction (parent above child). A message the requirement words for a failure appears next
  to the thing it is about, with the stated words. After sign-out, going Back never shows the signed-in page or
  name again (the page re-reads the signed-in state). A filter uses every field the requirement says it filters by.
- One owner per rule. Every rule of the domain lives in exactly ONE module that every caller uses -- never a second
  implementation that can drift from it (v160c: delivered apps had two formula engines that disagreed on an empty
  referenced cell, twelve helpers that looked a repository up, a router mounted twice and handlers no request reached):
  - Computation (formula evaluation, totals, the check of a value): the server computes and stores the results and the
    page shows them; where the page must compute too (a preview while typing), it imports the same shared module --
    never a copy with rules of its own.
  - Lookup of a record that is unique only per owner (a repository per account or organization, a worksheet per
    workbook): by owner AND name through ONE helper, never a fallback to the name alone (with two records of that name
    it returns the wrong one); a route without the owner in its path takes the record's id, not its name.
  - Routing: each router is mounted once, and one method and path has exactly one handler (Express answers with the
    first match: a second registration of the same path, or a literal path after a matching `:param` path, is dead
    code a later change edits in vain). A new endpoint goes into the router file that owns its resource.
  - Who the request is: one authentication middleware, used by every protected route. When the requirement lets a
    session end other than by its own sign-out (revoked from another device, expired, ended by a password change), the
    session row carries that state and the middleware rejects an inactive session on EVERY request -- a flag only the
    sessions page reads ends nothing.
  Before adding a rule, find its owner in the files you were shown and extend it; a second helper doing the same job
  is a defect even while both work.
- Later work never breaks earlier work: a later requirement may add to a page or a seed record, but it keeps the
  names, roles and states an earlier requirement stated for them (a button keeps its stated name when a later
  requirement hangs a menu on it).
- Operations on tabular data (sort, filter, move, copy) work on the DATA rows: a row the user declares a header stays
  in place and visible; values compare by their type (numbers as numbers, dates as dates, text as text), never all as
  text; references that point into moved data move with it, as the same operation does when a user moves cells.
  Whether an editor opens in place or as a dialog is the requirement's decision, not yours.
- An editable grid behaves like a spreadsheet unless the requirement says otherwise. A cell is never itself
  contentEditable: a selected cell shows its value; typing on it opens an input that REPLACES the content (double-click
  or F2 edits with the old content kept); Enter commits and moves down, Tab commits and moves right, an arrow key
  commits and moves, Escape cancels and restores -- each commit is sent ONCE (the blur that follows a commit does not
  send it again), and a commit compares with the stored content, not the displayed value (typing a formula's result
  over the formula replaces it). Shift+click and Shift+arrows extend the selection; the selected range and the
  active tab are drawn visibly, not only marked with aria-selected. The formula bar mirrors the active cell while
  typing; after a refused commit both show the last saved value. Undo / Redo are disabled when there is nothing to
  undo / redo. State that belongs to one worksheet (its filters, sort, rules) is stored per worksheet. A write is
  awaited before the view refetches, so a refetch never overwrites the change just made. An error message goes away
  on the next success and is shown once. A stated default-name pattern is followed exactly ("the first unused" is
  not "count + 1"). A CSV import strips a UTF-8 BOM. Sort options and filter values come from the selected range and
  its declared header row, never from worksheet row 1 or cells outside the range. A new validation rule constrains
  later input in its own range only: it never re-checks existing values into blocking writes, nor touches cells
  outside its range. Dialog fields that expect a number refuse anything else.
- References survive structural edits: inserting or deleting rows / columns shifts every reference and every range
  held by formulas and derived objects (summaries, pivots, filters, rules) and keeps each `$` marker as written; a
  range whose end row / column is deleted shrinks; only a reference whose own cell is deleted becomes the stated
  error (e.g. #REF!), never silently rewired to a neighbour. Sorting or moving rows moves their formulas' references
  with the data. Formula errors follow the stated rules: a malformed expression and a circular reference (also
  through a range) show the stated error, and an error in a referenced cell propagates -- it never turns into 0. A
  seeded formula points at the cells it is stated with: if the seed block is placed elsewhere, its references move
  with it.
- A menu, dropdown or popover that the requirement says APPEARS on hover or click is closed until then: its entries are
  not rendered (or carry `hidden`), and it opens the way the requirement says (hover, or click). Never build a menu
  that is always open: the links of an open menu steal the clicks meant for the
  page's own entry of the same word, and it is not what the requirement describes. The same holds for anything the
  requirement says a control OPENS or an action CLOSES (a search field of a selector, a panel): it is rendered only while
  open -- absent before the opening action, gone after the closing one (Escape, a choice, as stated), together with
  what it shows (its messages, its list). Escape and a click outside close any open menu or popover as well, also
  when the requirement names another way to close it (a person expects both). The one exception are the action
  buttons INSIDE a repeated item (card, row) that the design reveals on hover: those stay in the layout and fade with
  opacity, so keyboard users can reach them without a mouse.
- Feedback must outlive navigation: when the requirement says an action "shows a message" and the action also
  navigates, the message has to be visible AFTER the navigation. A message set right before `navigate(...)` dies with
  the page. Pass it along (`navigate("/", { state: { notice: "..." } })`) and let the shared layout render
  `location.state.notice` in an element with the role the requirement names for that message ("a status message" ->
  role="status"); role="alert" only when it names none.
- A validation or server error message shown to the user is rendered inside an element with the role the requirement
  names for it (status, alert ...); when it names none: role="alert".
- Semantics: the role the requirement STATES wins (a navigation entry it calls a link stays an <a> even when styled
  as a tab; role="tab" only where it says tab; a field it calls a search box is <input type="search">); otherwise
  use the most specific native element or ARIA role for every widget the requirement describes, give it the
  requirement's own name as its accessible name (a name the requirement GIVES -- "a dialog named X", "the button `Y`";
  a describing phrase does not turn a panel into a dialog of that name, see the rule on dialogs above), and render what the requirement describes as PRESENT on a page from the first
  paint with a non-zero size (an empty state is still visible), so that role- and label-based locators can find it.
  This does not apply to what the requirement says appears only after an action (menus, dialogs, popovers).
- Keyboard shortcuts on something that is not a text field (a grid, a board, a list): copy, cut and paste arrive as
  the document's `copy` / `cut` / `paste` events -- handle those, not a keydown of C / X / V: read
  `event.clipboardData.getData("text/plain")`, rows split on /\\r?\\n/ (the browser hands over "\\r\\n" on some
  systems) and columns on "\\t"; write with `event.clipboardData.setData("text/plain", ...)` + `event.preventDefault()`.
  Leave the events alone when their target is an input, textarea or contenteditable: the field handles its own.
  What the application copies richer than text (formulas, styles) travels in the same copy event under a type of its
  own -- `event.clipboardData.setData("application/x-<app>-cells", JSON.stringify(...))` -- and a paste that carries
  that type uses it; a copy made anywhere else replaces the whole clipboard, so text without it came from outside and
  is pasted as plain values (a text comparison cannot tell the two apart). A menu command "Paste" gets no paste event:
  it reads `navigator.clipboard.readText()` and runs the SAME paste as the shortcut. A cut clears nothing when it
  happens: it marks the source, and the paste that follows moves the data -- the source is cleared only once the
  target is saved; when the paste is rejected, source and target both stay as they were. Undo / redo (Ctrl+Z, Ctrl+Y)
  are keydown handlers that accept ctrlKey or metaKey and compare `event.key.toLowerCase()` (a press of "Control+Z"
  arrives as key "Z"); inside a field that holds an edit not yet committed they are the field's own text undo,
  everywhere else -- including a field whose value was just committed -- they undo the application's last change.
  (ADR-099)
- A grid (a spreadsheet: role grid / row / gridcell / rowheader / columnheader) is not a list of repeated items: a
  cell is named by its coordinate (aria-label "B2") and shows its value as its text, with no heading, no button and
  no card click handler inside it; the rule on repeated items above does not apply to it. A structural edit (insert or
  delete rows or columns, move) shifts EVERY stored reference to cells -- formulas, and the ranges held by derived
  objects (summaries, filters, validation rules, charts) -- so what is recomputed afterwards uses the adjusted range.
- A computation rule the requirement states -- how empty, blank or non-numeric inputs count, what is skipped, what is
  counted -- is implemented exactly as stated: an empty input is never silently turned into zero or into text a
  function then counts (ADR-099 section 86). Unless the requirement says otherwise, plain arithmetic on an empty
  referenced cell treats it as a spreadsheet does (as 0), and clearing a cell (Delete, an empty commit, undo) recalculates
  every formula that references it at once, on the server and on the page; only a reference whose cell was REMOVED by a
  structural edit becomes the reference error -- a cleared cell is empty, never a broken reference.
- A grid works over its whole extent: rendering (or scrolling / virtualising), coordinates, selection (click,
  Shift+click, Shift+arrows, drag), keyboard moves, paste and Delete reach every column and row the requirement or the
  stored data uses -- never a fixed first screenful; Delete clears every cell of the selected range and keeps it selected.
- A page appears complete. Someone who looks right after the click that opened a page must already see its list,
  its item's name, the signed-in user -- a page that renders first and fetches on mount shows them missing. So
  server data is read through frontend/src/api/store.ts, never with useEffect + fetch + useState in a page:
  `const shelves = useData<Shelf[]>('/shelves')` returns the cached copy in the same render and refreshes it;
  App.tsx calls `preload([...])` once at module level (start-up) with every list path the pages read and '/me'; a details
  page first shows what the list already holds (`peek('/shelves')?.find(...)`), so the list endpoint returns every field
  the details page shows first; a link or control that opens a page whose data is NOT in the start-up preload (a
  record's own sub-data under a parameterised path: its files, commits, members, one workbook's cells) keeps its
  href but calls `loadNow([...that page's paths])` in its onClick, before the router navigates -- a plain <Link> alone
  opens that page empty and fills it a moment later (github v141: 45 "was not on the page when it opened" failures);
  login happens INSIDE the submit handler, synchronously, with the store's `postNow`
  and `loadNow` (never `await` in a login handler): `const user = postNow('/login', {...}).user` (it throws on a
  failed login: show the error, stay), `loadNow([...])` for what the next page shows (a list that needs a signed-in
  user was not loaded before), `put('/me', user)`, then navigate -- a person who looks, or opens another page, right
  after pressing the button is already signed in and sees their name (an awaited request is aborted by that
  navigation: the sign-in never happens; ADR-099); logout does
  `reset()` then `put('/me', null)`; the signed-in state is never re-requested on every navigation; a create / edit /
  delete awaits `refresh([...affected paths])` -- the lists AND the details path it navigates to -- BEFORE it navigates
  (it rejects when the server fails: show the error, stay). `put()` holds exactly what a GET of that path returns, never
  a create's partial answer in place of the record's details. No "Loading..." text in place of content the store already has.
- Lessons of the v145 explorations (ADR section 210) -- each was a real defect of a delivered application. Each one is
  a default: what the requirement itself states wins, and the earlier rules of this text keep their priority:
  - Sessions: one account may hold any number of sessions at once (several browsers, a test that never signs out).
    Unless the requirement itself limits an account to one session, sign-in NEVER fails because the account already has an active session; an "unavailable" account is one whose
    own status says so (disabled, unverified, locked), never one that is signed in elsewhere. A password reset or
    change does not block the next sign-in with the new password.
  - Accessible names: a control's accessible name equals its visible label (a grid cell keeps its coordinate name as
    stated above; a name the requirement states for an element wins). Never nest a button or link inside a
    columnheader, rowheader, gridcell, tab, link or button (the outer name becomes "A Filter ..."): put the extra
    control beside it, or give the outer element aria-label equal to its own label.
  - Seed rows: INSERT OR IGNORE only skips a row that violates a UNIQUE / PRIMARY KEY constraint; a table without one
    gets a duplicate on every start-up. Seed each record only when a SELECT by its natural key finds nothing (or
    declare that key UNIQUE); a grant a later requirement states for the same subject and resource still updates the
    existing row ("Seed rows that grant" above). A seeded record keeps EXACTLY the name the requirement states -- never a suffix or a
    rename to avoid a clash; names are unique per owner (per account, organization, workbook), not globally.
  - Every value a scenario's GIVEN states for a seeded record (a branch ahead of main, a reviewer's role, a stated
    account, rule or record) is seeded as stated; a seeded role lets that account do what the scenario has it do.
    The rules on seeded state above still decide: a later state that only a scenario's OUTCOME shows (not stated by a
    GIVEN or a seed sentence as already true) is reached through the user's steps, conflicting starting states follow
    the majority rule, and a role the requirement states for an account wins.
  - Roles and enumerated values are compared case-insensitively (or stored in one canonical case): a seeded 'admin'
    or 'write' grant counts as Admin / Write; a status is stored and shown in one form ("Open" for seeded and new
    records alike). Roles are a list of allowed roles per action as the requirement states, never a rank ladder unless the requirement
    says the roles are cumulative.
  - Server authorization: every permission helper that returns a Promise is awaited (`if (canWrite(...))` on a
    Promise is always true). Every write checks session and role on the server, as the requirement states, also for
    close, reopen, edit title, ready, request reviewer, approve (an author never approves their own change).
  - Every SQL statement names only columns its table declares (CREATE TABLE); a missing column is a 500 on every call.
  - The server enforces every limit the requirement states (lengths, password length, required fields, terms,
    uniqueness of an open item per pair) exactly like the form does.
  - A list filtered by permission includes every record the user can open (direct grants and team grants too): the
    list and the details page use ONE access rule (one helper), never two that drift apart.
  - Lists key and de-duplicate records by id, never by name: two records with the same name under different owners
    (a fork and the original, a user's and an organization's) are both listed.
  - A record's "Updated" time changes whenever the record or anything it holds changes (an edit, a new commit, a
    comment), and is shown in the user's local time, never a raw database timestamp.
  - A search box in the shared header searches the whole application wherever the user is; a scoped search is a
    separate, labelled control.
  - A read-only view (a commit's changed files, a comparison, a history) needs only READ access: never gate it behind
    a write permission (unless the requirement reserves that view for a role -- then that role decides).
  - A name that may contain "/" (a branch "feature/api-v2", a file path) is passed through encodeURIComponent in
    every client URL and API path, and the server route decodes it (or takes the rest of the path); a page reached
    with such a name shows the same content as any other name.
  - Data scoped by a selection (the branch, the base and compare of a comparison) follows that selection everywhere on
    the page, and a page opened with that selection in its URL (?base=...&compare=...) applies it on load.
  - Dates are compared as dates (Before / After / between): never Number() of a date string.
  - Computations read a cell's computed value, never its formula text -- also sort keys, filter conditions and the
    filter's offered values, summaries and exports of values: ONE helper gives the computed value everywhere.
  - A filter (and any rule tied to a selection) stores the range it was created on and acts only inside it.
  - A drag or Shift-gesture changes only the SELECTION, whatever the cells hold (values, validation rules): data
    moves only through the move / cut / paste actions the requirement names.
  - The formula bar commits its text like the cell editor does (Enter, Tab, a click on another cell or elsewhere).
  - Undo and redo act on the object the user is working on (the current sheet / record): one Undo never restores
    another sheet's state.
  - A create or import that the requirement says opens the new record ends ON that record's page (navigate with
    the id the server returned), not back on the list.
  - Structural edits: inserting or deleting rows or columns shifts absolute ($A$1) and mixed references exactly like
    relative ones -- "$" only keeps a reference fixed when a formula is copied or filled. Deleting rows inside a
    referenced range shrinks the range (formulas, filters, validation rules, summary sources); only a reference whose
    every cell is deleted becomes #REF! -- unless the requirement states another policy for ranges that touch a
    deleted cell, which then wins.
  - Grid editing like a desktop spreadsheet, unless the requirement says otherwise: typed text is committed (and saved to the server) on Enter, Tab, an
    arrow key, a click on another cell AND a click anywhere else on the page; Enter then selects the cell below, Tab
    the cell to the right; Delete and Backspace on a selection clear it; Shift+click and Shift+arrow extend the
    selection to a range. Ctrl+C / Ctrl+X write the copied text to the system clipboard (the copy/cut event's
    clipboardData) as well as the app's own buffer, and every paste path (Ctrl+V, context menu) pastes the same,
    most recent copy; a cut clears its source after the paste.
  - Dialogs and menus: opening one moves focus into it (its first field or button); Escape closes it from any focus
    inside; Enter in a field submits it; a menu or popover also closes on Escape and on a click outside.
  - Undo records every user change the requirement lets the user undo, including sort and filter, as its own step;
    one Undo never reverts two actions.
  - A CSV export writes every row of the used range with the same number of fields (trailing empty cells included).
  - A workbook reopens on the sheet the user last had active when the application stores that state.
  - A field cleared back to its empty choice ("(none)") is saved as empty and stays empty after a reload.
  - Signed in, the header shows the account menu and never "Sign in" / "Sign up"; a successful registration shows a
    visible success message before or on the page it leads to.
  - The page a sign-in lands on is the signed-in WORKSPACE, not the visitor's welcome page: no signed-out wording, and
    every place the requirement's signed-in scenarios start from is reachable from it by visible links with the
    stated names (the user's organizations and their Teams / People / Repositories, settings, the user's records).
    A person who only reads the requirement and clicks named links from that page reaches every scenario's start.
  - When one submission breaks several stated rules at once, the message a scenario's THEN states for exactly those
    inputs is the one shown ("enters an existing identifier, leaves the display name empty" -> "… already exists", not
    the empty-field message): order the checks so every scenario's stated message wins for its own inputs.
  - A message the requirement states for a failure ("displays “… already exists”") is rendered verbatim on the page
    where the action was taken, next to the form or field -- not only returned as an HTTP status.
  - In an application with accounts (the requirement signs users in), every page (settings, record pages, forms,
    error pages) renders the shared header with the account control and its menu (Sign out and the account entries),
    so a user can go on from wherever a flow ended; an application whose requirement never signs anyone in gets no
    account control, no sign-in and no sign-out. A control a scenario's
    WHEN opens right after another step ("signs in, opens Settings") is visible on the page that step leaves the user
    on -- not hidden behind a menu, tab or page the WHEN does not name; records shown on a page (organizations,
    repositories, teams) are links to their pages unless the order of kinds above gives another kind.
  - A choose-one control (the requirement says the user selects or chooses an option in a labelled control or in a
    combobox) takes, in this order: the role or interaction the requirement states for it (a named button that opens
    a list whose entries are clicked: that button plus a listbox of role-option entries); the kind the existing
    application's control already has; otherwise a native <select> with a visible <label> tied to it (its role is
    combobox, its entries are <option>s).
  - On one page, no two form controls share an accessible name: label each control after what it sets for which
    target ("Role for <name>", "New member role") unless the requirement names them itself.
  - Each page the requirement names has its stated controls in the UI (forms, menus, tabs as links, filters); an
    endpoint without its control is a missing feature. An unknown id or name shows a visible not-found message.
"""

EVOLUTION_DATA_LIFECYCLE = (
    "EVOLUTION DATA LIFECYCLE: The delivered package starts without database.db; the same startup code must also run on "
    "the database an older version of this application created, and run again on its own database. (1) Create every "
    "table with CREATE TABLE IF NOT EXISTS before any index, trigger, migration or seed that uses it. (2) A column added "
    "to an existing table: read PRAGMA table_info and only when the column is missing run ALTER TABLE ... ADD COLUMN ... "
    "DEFAULT ..., then backfill existing rows, in one transaction; before a case-insensitive UNIQUE index, look for rows "
    "that differ only by letter case and resolve them without deleting user data. (3) This round's pre-provisioned "
    "records go in as one versioned data migration run once inside one transaction and recorded as applied under a "
    "stable key; records several scenarios share (an account, an organization) are created once and referenced; never "
    "re-detect a seeded record by a display name a user can rename; a restart never re-creates or resets a record a user "
    "renamed, changed or deleted."
)

V166_LINES = (
    "- A control the requirement says a header, row or cell provides is rendered INSIDE that element: a column "
    "header's control lives inside that column's header cell, within the element with role grid, never in a separate "
    "toolbar outside it (the header cell then keeps an aria-label equal to its own label, as the accessible-name rule "
    "above says).",
    "- A grid cell's text content is exactly its value: decorations (dropdown arrows, markers, icons) never add "
    "characters to the cell's text; draw them with CSS or with an icon element outside that text (aria-hidden alone does "
    "not remove text from the cell's text content).",
    "- A page never shows a not-found or empty state while its data is still loading: render the page shell (headings, "
    "navigation links such as a record's tabs) at once from what is already known, and show not-found only after the "
    "data request has completed and confirmed that the record does not exist.",
    "- Unless the requirement or the kind an existing control already has decides otherwise (the order of kinds above), "
    "a pure navigation entry (a control whose only effect is to open another page: the requirement's entry links, a "
    "sign-in entry, tabs) is a link (an a element with href, role link), not a button; controls that submit or change "
    "data keep the role the requirement states even when a new page opens afterwards. A control the requirement places "
    "in the main content is inside the main landmark (a copy elsewhere, such as in the header, does not replace it).",
)
V166_CONVENTIONS = "".join(line + "\n" for line in V166_LINES)

def v166_conventions() -> str:
    from .settings import setting_on

    if not setting_on("conventions_v166"):
        return ""
    if not setting_on("conventions_v167"):
        return V166_CONVENTIONS
    return (V166_CONVENTIONS + V167_CONVENTIONS + (CHOICE_SELECT_CONVENTION if setting_on("choice_select_hybrid") else "")
            + (NAV_ENTRY_CONVENTION if setting_on("nav_entry_data_driven") else "")
            + (STATE_DISABLES_CONVENTION if setting_on("state_disables_everywhere") else "")
            + (V168X_CONVENTIONS if setting_on("conventions_v168x") else ""))

V167_LINES = (
    "- Unless the requirement states another role or interaction for it (for example a named button that opens a list "
    "of options) or the existing application already renders that control as another kind (it keeps it), a control "
    "from which the visitor chooses one of a fixed set of options (the requirement says choose "
    "an option from / in a labelled control, or names a combo box) is a native select element with a visible label "
    "element tied to it (its role is combobox and its options are option elements).",
    "- Unless the requirement names it, describes another form or places it in a container it names, an interface that "
    "a menu item opens (a management, save or settings interface) is a dialog (role dialog) whose accessible name is "
    "that menu item's label (the one exception to the rules above that a dialog name is never invented and that a "
    "describing phrase gives a panel no dialog); the controls "
    "in it that the visitor clicks take their kind in the order above (the role the requirement states, the kind an "
    "existing control has, otherwise a button), and a dialog also offers a "
    "button named exactly Close that closes it (in addition to the actions the requirement states; a Cancel button "
    "does not replace it). Entries the visitor clicks or chooses inside such an interface (for example saved items) "
    "are buttons named by the entry, not options or menu items, unless the requirement names a list, listbox or "
    "options there or an earlier stage already renders them as another kind (it keeps it); the options of a "
    "choose-one control (a native select, or the shared choose-one component when one is given) stay as they are.",
)

V167_CONVENTIONS = "".join(line + "\n" for line in V167_LINES)

NAV_ENTRY_CONVENTION = (
    "- A page the requirement places under a record (a record's settings, a list that belongs to it) is also offered "
    "in that record's own navigation (its tabs or section links), in addition to every entry a scenario reaches from "
    "its start page (keep those), so the page is reachable both from where a scenario starts and from the record.\n"
)

STATE_DISABLES_CONVENTION = (
    "- When the requirement says a state of a record makes some controls unavailable (to every user, or to some "
    "users), every page that renders such a control for that record or for what it contains honours that state -- its "
    "lists and detail pages as well as its main page -- and the server refuses the same actions.\n"
)

V168X_LINES = (
    "- A list the requirement calls a menu (an account menu, a control's menu) has role menu while it is open. Its "
    "entries take their kind in the order above -- the kind the requirement gives them (an entry it calls a link stays "
    "a link), then the kind an existing entry already has; otherwise an entry that takes the visitor to another page "
    "is a link (an <a> inside the menu, never role menuitem) and only an entry that acts in place (opens an interface, "
    "runs a command) is a menu item.",
    "- When a THEN says the page displays one thing or another for the same outcome (A or B), render exactly one of "
    "them -- the one that still shows after a reload when the THEN also asks for the state after reload -- and no "
    "second notice of that same outcome (this narrows the rule that an action shows a confirming notice).",
    "- The page where a signed-out visitor signs in also offers, inside its main content, the entry links the "
    "requirement lists for the account-access page (its sign-in and sign-up entries, its password-recovery entry) next "
    "to the form (entries the shared header already offers stay there too).",
    "- Every status value the requirement names for a record (for example an archived state and the normal state it "
    "returns to) is a visible marker where the record shows its status, in whichever state the record is -- the "
    "normal state is shown by its word, never by an empty space.",
    "- A count the requirement says is displayed stays displayed at every value, zero included; it is never hidden "
    "because it is zero. When the requirement gives such a count no label of its own, it is one text of its own: the "
    "number followed by the requirement's word for what is counted, singular for one and plural otherwise, not a bare "
    "number beside an icon (a count the existing application already shows keeps its form).",
    "- When the requirement lists the controls a dialog or panel contains, every control it lists without a "
    "condition is present in every state of it, including when it shows an existing item, and a control it ties to a "
    "condition (a mode, a choice, a step) follows that condition; controls listed for an existing item are added, "
    "never a replacement; a text box that holds the item's value shows the stored value as its own value.",
    "- In a choose-one control that filters a list, when the requirement does not name the option that clears the "
    "filter, that option is named All followed by the plural of what the filter chooses among (a filter of statuses: "
    "All statuses), not a placeholder in brackets or dashes.",
)
V168X_TAIL = ("- These presentation rules (and the ones above) are part of implementing what the requirement states -- how a "
              "stated count, state, menu, dialog or control is shown -- not added features.\n")
V168X_CONVENTIONS = "".join(line + "\n" for line in V168X_LINES) + V168X_TAIL

CHOICE_SELECT_TSX = """function ChoiceSelect({ id, label, value, options, onChange, disabled }: {
  id: string; label: string; value: string; options: { value: string; label: string; disabled?: boolean }[];
  onChange: (value: string) => void; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <span style={{ position: 'relative', display: 'inline-block' }}>
      <label htmlFor={id}>{label}</label>{' '}
      <select id={id} value={value} disabled={disabled} aria-expanded={open && !disabled} aria-controls={`${id}-list`}
        onChange={(e) => { if (!disabled) onChange(e.target.value); setOpen(false); }}
        onMouseDown={(e) => { if (disabled) return; e.preventDefault(); e.currentTarget.focus(); setOpen((o) => !o); }}
        onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false); }}
        onBlur={() => setOpen(false)}>
        {options.map((o) => <option key={o.value} value={o.value} disabled={o.disabled} aria-hidden="true">{o.label}</option>)}
      </select>
      {open && !disabled && (
        <ul id={`${id}-list`} role="listbox" style={{ position: 'absolute', left: 0, top: '100%', zIndex: 50,
          background: '#fff', border: '1px solid #999', margin: 0, padding: 0, listStyle: 'none' }}>
          {options.map((o) => (
            <li key={o.value} role="option" aria-selected={o.value === value} aria-disabled={o.disabled || undefined}
              style={{ padding: '2px 8px' }} onMouseDown={(e) => e.preventDefault()}
              onClick={() => { if (!o.disabled) { onChange(o.value); setOpen(false); } }}>{o.label}</li>
          ))}
        </ul>
      )}
    </span>
  );
}"""
CHOICE_SELECT_CONVENTION = (
    "- Build every new or changed native-select choose-one control of the line above with this one shared component "
    "(put it in one shared file and import it; keep the label text, the option texts and the value the requirement "
    "gives; never add an accessible name or label to its list): it stays a native select with role combobox and a "
    "visible label, and pressing it also shows its options as clickable role-option entries; pass disabled when the "
    "requirement makes the control unavailable, and disabled on an option the requirement makes unavailable.\n"
    "```tsx\n" + CHOICE_SELECT_TSX + "\n```\n"
)

_EVOLUTION_ROUND = False

def set_evolution_round(active: bool) -> None:
    global _EVOLUTION_ROUND
    _EVOLUTION_ROUND = bool(active)

def evolution_round() -> bool:
    return _EVOLUTION_ROUND

def evolution_conventions() -> str:
    if not _EVOLUTION_ROUND:
        return ""
    from .seed_foundation import idempotent_head

    return f"- {EVOLUTION_DATA_LIFECYCLE}{idempotent_head()}\n"
