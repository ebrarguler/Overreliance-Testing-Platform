"""
A small in-memory stand-in for the pymongo collections the app uses, so
route, allocation and export tests run without a database. Supports only the
operations the code paths under test use: equality / `$in` / `$type`
queries, `$set` / `$setOnInsert` / `$inc` updates, upserts, and unique
(optionally compound and partial) indexes. Every operation holds a lock, so
single operations are atomic across threads as they are on a real server.
"""

import copy
import threading

from pymongo.errors import DuplicateKeyError

MISSING = object()

_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
}


def _get(doc, dotted):
    for part in dotted.split("."):
        if not isinstance(doc, dict) or part not in doc:
            return MISSING
        doc = doc[part]
    return doc


def _matches_value(value, cond):
    if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
        for op, arg in cond.items():
            if op == "$in":
                if value is MISSING or value not in arg:
                    return False
            elif op == "$type":
                if value is MISSING or not _TYPE_CHECKS[arg](value):
                    return False
            else:
                raise NotImplementedError(op)
        return True
    return value is not MISSING and value == cond


def matches(doc, query):
    return all(_matches_value(_get(doc, k), v) for k, v in (query or {}).items())


class _Result:
    def __init__(self, upserted_id=None, matched_count=0):
        self.upserted_id = upserted_id
        self.matched_count = matched_count


class FakeCollection:
    def __init__(self):
        self.docs = []
        # index name -> (tuple of fields, partial filter or None)
        self.unique = {}
        self._lock = threading.RLock()

    # --- indexes ---------------------------------------------------------

    def create_index(self, keys, unique=False, name=None, partialFilterExpression=None):
        fields = (keys,) if isinstance(keys, str) else tuple(k for k, _ in keys)
        if unique:
            self.unique[name or "_".join(f"{f}_1" for f in fields)] = (
                fields,
                partialFilterExpression,
            )

    def index_information(self):
        return {name: {} for name in self.unique}

    def drop_index(self, name):
        del self.unique[name]

    def _check_unique(self, doc, ignore=None):
        for fields, partial in self.unique.values():
            if partial is not None and not matches(doc, partial):
                continue
            key = tuple(_get(doc, f) for f in fields)
            if len(fields) == 1 and key[0] is MISSING:
                continue
            for other in self.docs:
                if other is ignore:
                    continue
                if partial is not None and not matches(other, partial):
                    continue
                if tuple(_get(other, f) for f in fields) == key:
                    raise DuplicateKeyError(f"dup {fields}")

    # --- reads -----------------------------------------------------------

    def find_one(self, query=None, projection=None):
        with self._lock:
            for d in self.docs:
                if matches(d, query):
                    return copy.deepcopy(d)
        return None

    def find(self, query=None, projection=None):
        with self._lock:
            found = [copy.deepcopy(d) for d in self.docs if matches(d, query)]
        if projection:
            for d in found:
                for k, v in projection.items():
                    if v == 0:
                        d.pop(k, None)
        return _Cursor(found)

    def count_documents(self, query):
        with self._lock:
            return sum(1 for d in self.docs if matches(d, query))

    # --- writes ----------------------------------------------------------

    def insert_one(self, doc):
        with self._lock:
            self._check_unique(doc)
            self.docs.append(copy.deepcopy(doc))

    @staticmethod
    def _apply(doc, update):
        for k, v in update.get("$set", {}).items():
            doc[k] = copy.deepcopy(v)
        for k, v in update.get("$inc", {}).items():
            doc[k] = doc.get(k, 0) + v

    def update_one(self, query, update, upsert=False):
        with self._lock:
            for d in self.docs:
                if matches(d, query):
                    self._apply(d, update)
                    return _Result(matched_count=1)
            if upsert:
                doc = {
                    k: v for k, v in query.items()
                    if not (isinstance(v, dict) and any(x.startswith("$") for x in v))
                }
                doc.update(copy.deepcopy(update.get("$setOnInsert", {})))
                self._apply(doc, update)
                self.insert_one(doc)
                return _Result(upserted_id="new")
        return _Result()

    def find_one_and_update(self, query, update, sort=None, return_document=None):
        with self._lock:
            candidates = [d for d in self.docs if matches(d, query)]
            if sort:
                for field, direction in reversed(sort):
                    candidates.sort(key=lambda d: d[field], reverse=direction < 0)
            if not candidates:
                return None
            self._apply(candidates[0], update)
            return copy.deepcopy(candidates[0])


class _Cursor(list):
    def sort(self, field, direction=1):
        return _Cursor(sorted(self, key=lambda d: d.get(field), reverse=direction < 0))


class FakeDB(dict):
    """name -> FakeCollection, created on first use (thread-safe)."""

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()

    def get_collection(self, name):
        with self._lock:
            return self.setdefault(name, FakeCollection())
