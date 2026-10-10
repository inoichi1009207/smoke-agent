const DELAY_MS = 150;
const items = [{ id: 1, name: 'Shelf A' }, { id: 2, name: 'Shelf B' }];
let slowNextPrivate = false;
const signedIn = (req) => /(?:^|;\s*)sid=1(?:;|$)/.test(req.headers.cookie || '');
const later = (fn, ms = DELAY_MS) => setTimeout(fn, ms);

module.exports = function mount(app) {
  app.get('/api/items', (req, res) => { const snapshot = items.map((i) => ({ ...i })); later(() => res.json(snapshot)); });
  app.post('/api/items', (req, res) => { items.push({ id: items.length + 1, name: req.body.name }); later(() => res.json({ ok: true }), 20); });
  app.get('/api/broken', (req, res) => later(() => res.status(500).json({ error: 'broken on purpose' })));
  app.get('/api/me', (req, res) => later(() => (signedIn(req) ? res.json({ nickname: 'Test User' }) : res.status(401).json({ error: 'signed out' }))));
  app.post('/api/slow-private', (req, res) => { slowNextPrivate = true; res.json({ ok: true }); });
  app.get('/api/private', (req, res) => {
    const ok = signedIn(req);
    const ms = slowNextPrivate ? 800 : DELAY_MS;
    slowNextPrivate = false;
    later(() => (ok ? res.json([{ id: 9, name: 'Secret A' }]) : res.status(401).json({ error: 'signed out' })), ms);
  });
  app.post('/api/login', (req, res) => { res.setHeader('Set-Cookie', 'sid=1; Path=/'); later(() => res.json({ nickname: 'Test User' })); });
  app.post('/api/logout', (req, res) => { res.setHeader('Set-Cookie', 'sid=; Path=/; Max-Age=0'); later(() => res.json({ ok: true }), 20); });
};
