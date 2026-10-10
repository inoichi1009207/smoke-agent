const path = require('path');
const rt = require(path.resolve(process.argv[2], 'src/database/db_runtime.js'));
(async () => {
  await rt.exec('CREATE TABLE IF NOT EXISTS t (k TEXT, v INTEGER)');
  const slow = (ms) => new Promise((r) => setTimeout(r, ms));
  const results = await Promise.allSettled([1, 2, 3, 4, 5].map((i) => rt.withTransaction(async (tx) => {
    await tx.run('INSERT INTO t VALUES (?, ?)', ['par', i]); await slow(20); return i;
  })));
  console.log('parallel', results.map((r) => r.status).join(','));
  const nested = await rt.withTransaction(async (tx) => {
    await tx.run("INSERT INTO t VALUES ('outer', 1)");
    await rt.withTransaction(async (inner) => inner.run("INSERT INTO t VALUES ('inner', 1)"));
    await rt.run("INSERT INTO t VALUES ('plain-in-tx', 1)");
    return 'ok';
  });
  console.log('nested', nested);
  const rb = await rt.withTransaction(async (tx) => { await tx.run("INSERT INTO t VALUES ('rolled', 1)"); throw new Error('boom'); }).catch((e) => e.message);
  const [tx1, plain] = await Promise.allSettled([
    rt.withTransaction(async (tx) => { await tx.run("INSERT INTO t VALUES ('tx-rolls', 1)"); await slow(50); throw new Error('x'); }),
    (async () => { await slow(10); return rt.run("INSERT INTO t VALUES ('plain-outside', 1)"); })(),
  ]);
  console.log('rollback', rb, tx1.status, plain.status);
  let late;
  await rt.withTransaction(async (tx) => {
    await tx.run("INSERT INTO t VALUES ('a-in', 1)");
    late = new Promise((resolve) => setTimeout(() => resolve(rt.run("INSERT INTO t VALUES ('a-late', 1)")), 30));
  });
  const b = rt.withTransaction(async (tx) => { await tx.run("INSERT INTO t VALUES ('b-rolled', 1)"); await slow(80); throw new Error('b'); });
  await Promise.allSettled([late, b]);
  const rows = await rt.all('SELECT k, COUNT(*) n FROM t GROUP BY k ORDER BY k');
  console.log(JSON.stringify(rows));
  process.exit(0);
})().catch((e) => { console.log('FAIL', e.message); process.exit(1); });
