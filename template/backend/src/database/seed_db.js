const { closeDb } = require('./init_db');
const { withTransaction } = require('./db_runtime');

async function seedDatabase() {
  return withTransaction(async ({ run, get, all, exec }) => {
    void run;
    void get;
    void all;
    void exec;

  });
}

if (require.main === module) {
  seedDatabase()
    .then(() => closeDb())
    .catch((error) => {
      console.error('Database seed failed:', error);
      process.exitCode = 1;
    });
}

module.exports = seedDatabase;
module.exports.seedDatabase = seedDatabase;
module.exports.seed = seedDatabase;
