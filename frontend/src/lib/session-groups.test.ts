// node --test --experimental-strip-types src/lib/session-groups.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { groupByRecency } from "./session-groups.ts";

// Fixed "now": 2026-08-26 14:00 local. Buckets are relative to local midnight,
// so building the fixtures from that same local midnight keeps the test from
// flipping buckets depending on the machine's timezone.
const NOW = new Date(2026, 7, 26, 14, 0, 0).getTime();
const MIDNIGHT = new Date(2026, 7, 26, 0, 0, 0).getTime();
const DAY = 86_400_000;

const at = (ms: number) => ({ updated_at: new Date(ms).toISOString() });

test("buckets by recency, in order, dropping empty groups", () => {
  const groups = groupByRecency(
    [
      at(NOW - 60_000),
      at(MIDNIGHT - DAY + 3600_000),
      at(NOW - 4 * DAY),
      at(NOW - 90 * DAY),
    ],
    NOW
  );

  assert.deepEqual(
    groups.map((g) => g.name),
    ["Today", "Yesterday", "Previous 7 Days", "Older"]
  );
  assert.equal(groups.every((g) => g.items.length === 1), true);
});

test("empty groups are not rendered", () => {
  const groups = groupByRecency([at(NOW - 60_000)], NOW);
  assert.deepEqual(
    groups.map((g) => g.name),
    ["Today"]
  );
});

test("local midnight is the Today boundary", () => {
  // One millisecond either side of midnight must not land in the same bucket.
  const groups = groupByRecency([at(MIDNIGHT), at(MIDNIGHT - 1)], NOW);
  assert.deepEqual(
    groups.map((g) => g.name),
    ["Today", "Yesterday"]
  );
});

test("an unparseable timestamp is kept, not dropped", () => {
  const groups = groupByRecency([{ updated_at: "not a date" }], NOW);
  assert.deepEqual(
    groups.map((g) => g.name),
    ["Older"]
  );
});

test("no input is not an error", () => {
  assert.deepEqual(groupByRecency([], NOW), []);
});
