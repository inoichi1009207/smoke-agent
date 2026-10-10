const { AsyncLocalStorage } = require('async_hooks');
const { getDb, initializeDatabase } = require('./init_db');

function normalizeParams(params) {
  if (Array.isArray(params)) {
    return params;
  }
  if (params === undefined) {
    return [];
  }
  return [params];
}

function runOnDatabase(database, sql, params = []) {
  return new Promise((resolve, reject) => {
    database.run(sql, normalizeParams(params), function handleRun(err) {
      if (err) {
        reject(err);
        return;
      }
      resolve({
        lastID: this.lastID,
        changes: this.changes,
      });
    });
  });
}

function getOnDatabase(database, sql, params = []) {
  return new Promise((resolve, reject) => {
    database.get(sql, normalizeParams(params), (err, row) => {
      if (err) {
        reject(err);
        return;
      }
      resolve(row || null);
    });
  });
}

function allOnDatabase(database, sql, params = []) {
  return new Promise((resolve, reject) => {
    database.all(sql, normalizeParams(params), (err, rows) => {
      if (err) {
        reject(err);
        return;
      }
      resolve(rows || []);
    });
  });
}

function execOnDatabase(database, sql) {
  return new Promise((resolve, reject) => {
    database.exec(sql, (err) => {
      if (err) {
        reject(err);
        return;
      }
      resolve();
    });
  });
}

const transactionContext = new AsyncLocalStorage();
let transactionQueue = Promise.resolve();

function openTransaction() {
  const store = transactionContext.getStore();
  return store && store.open ? store : null;
}

function afterOpenTransaction() {
  if (openTransaction()) {
    return Promise.resolve();
  }
  return transactionQueue;
}

async function run(sql, params = []) {
  const database = await initializeDatabase();
  await afterOpenTransaction();
  return runOnDatabase(database, sql, params);
}

async function get(sql, params = []) {
  const database = await initializeDatabase();
  await afterOpenTransaction();
  return getOnDatabase(database, sql, params);
}

async function all(sql, params = []) {
  const database = await initializeDatabase();
  await afterOpenTransaction();
  return allOnDatabase(database, sql, params);
}

async function exec(sql) {
  const database = await initializeDatabase();
  await afterOpenTransaction();
  return execOnDatabase(database, sql);
}

async function runTransaction(database, work) {
  await execOnDatabase(database, 'BEGIN IMMEDIATE TRANSACTION;');

  const store = { open: true, tx: null };
  const tx = {
    run: (sql, params = []) => (store.open ? runOnDatabase(database, sql, params) : run(sql, params)),
    get: (sql, params = []) => (store.open ? getOnDatabase(database, sql, params) : get(sql, params)),
    all: (sql, params = []) => (store.open ? allOnDatabase(database, sql, params) : all(sql, params)),
    exec: (sql) => (store.open ? execOnDatabase(database, sql) : exec(sql)),
  };
  store.tx = tx;

  try {
    const result = await transactionContext.run(store, () => work(tx));
    await execOnDatabase(database, 'COMMIT;');
    return result;
  } catch (error) {
    try {
      await execOnDatabase(database, 'ROLLBACK;');
    } catch (rollbackError) {
      error.rollbackError = rollbackError;
    }
    throw error;
  } finally {
    store.open = false;
  }
}

async function withTransaction(work) {
  const open = openTransaction();
  if (open) {
    return work(open.tx);
  }
  const database = await initializeDatabase();
  const turn = transactionQueue.then(() => runTransaction(database, work));
  transactionQueue = turn.catch(() => {});
  return turn;
}

module.exports = {
  run,
  get,
  all,
  exec,
  withTransaction,
};
