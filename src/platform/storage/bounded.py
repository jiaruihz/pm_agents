"""Exact disk-backed working sets; heap use does not track journal length.

These are ephemeral projections, never authoritative raw journals. SQLite owns
its unnamed temporary file and a fixed page cache; closing releases resources.
No eviction or approximate membership is allowed for financial deduplication.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, MutableMapping, MutableSet, ValuesView, ItemsView
import json
import sqlite3
from typing import Any


class _Values(ValuesView):
    def __iter__(self):
        for row in self._mapping._db.execute("SELECT value FROM items ORDER BY seq"):
            yield self._mapping._decode(row[0])


class _Items(ItemsView):
    def __iter__(self):
        for row in self._mapping._db.execute("SELECT key,value FROM items ORDER BY seq"):
            yield row[0], self._mapping._decode(row[1])


class DiskMap(MutableMapping[str, Any]):
    """Insertion-ordered mapping of local Python values with a bounded 256 KiB SQLite cache."""

    def __init__(self, rows: Iterable[tuple[str, Any]] = ()) -> None:
        self._db = sqlite3.connect("", check_same_thread=False)
        self._db.execute("PRAGMA cache_size=-256")
        self._db.execute("PRAGMA temp_store=FILE")
        self._db.execute("PRAGMA journal_mode=OFF")
        self._db.execute("CREATE TABLE items (seq INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, value TEXT NOT NULL)")
        for key, value in rows:
            self[key] = value

    @staticmethod
    def _decode(raw):
        import pickle
        return pickle.loads(raw)

    def __getitem__(self, key: str) -> Any:
        if not isinstance(key, str):
            raise KeyError(key)
        row = self._db.execute("SELECT value FROM items WHERE key=?", (str(key),)).fetchone()
        if row is None:
            raise KeyError(key)
        return self._decode(row[0])

    def __setitem__(self, key: str, value: Any) -> None:
        if not isinstance(key, str):
            raise TypeError("DiskMap requires string keys")
        import pickle
        encoded = pickle.dumps(value, protocol=5)
        self._db.execute("INSERT INTO items(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(key), encoded))

    def __delitem__(self, key: str) -> None:
        if not isinstance(key, str):
            raise KeyError(key)
        if not self._db.execute("DELETE FROM items WHERE key=?", (str(key),)).rowcount:
            raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self._db.execute("SELECT 1 FROM items WHERE key=?", (key,)).fetchone() is not None

    def __iter__(self) -> Iterator[str]:
        for row in self._db.execute("SELECT key FROM items ORDER BY seq"):
            yield row[0]

    def __len__(self) -> int:
        return int(self._db.execute("SELECT count(*) FROM items").fetchone()[0])

    def values(self):
        return _Values(self)

    def items(self):
        return _Items(self)

    def ordered_items(self):
        for key, payload in self._db.execute("SELECT key,value FROM items ORDER BY key"):
            yield key, self._decode(payload)

    def clear(self) -> None:
        self._db.execute("DELETE FROM items")

    def close(self) -> None:
        self._db.close()

    def __del__(self) -> None:
        if hasattr(self, "_db"):
            self._db.close()


class DiskSet(MutableSet[str]):
    """Exact string membership retained for the entire owner lifetime."""

    def __init__(self, values: Iterable[str] = ()) -> None:
        self._rows = DiskMap()
        self.update(values)

    def __contains__(self, value: object) -> bool:
        return value in self._rows

    def __iter__(self) -> Iterator[str]:
        return iter(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def add(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("DiskSet requires string identities")
        self._rows[value] = None

    def discard(self, value: str) -> None:
        try:
            del self._rows[value]
        except KeyError:
            pass

    def update(self, values: Iterable[str]) -> None:
        for value in values:
            self.add(value)

    def clear(self) -> None:
        self._rows.clear()

    def close(self) -> None:
        self._rows.close()


class DiskTupleSet(MutableSet):
    """Exact tuples of optional string identities without a growing Python set."""
    def __init__(self, values=()):
        self._keys = DiskSet(self._key(value) for value in values)

    @staticmethod
    def _key(value):
        if not isinstance(value, tuple) or any(not isinstance(x, (str, type(None))) for x in value):
            raise TypeError("DiskTupleSet requires tuples of optional string identities")
        return json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False)

    def __contains__(self, value):
        try:
            return self._key(value) in self._keys
        except (TypeError, ValueError):
            return False

    def __iter__(self):
        return (tuple(json.loads(key)) for key in self._keys)

    def __len__(self):
        return len(self._keys)

    def add(self, value):
        self._keys.add(self._key(value))

    def discard(self, value):
        self._keys.discard(self._key(value))

    def close(self):
        self._keys.close()


class DiskRows:
    """Reiterable local Python rows spooled to disk, optionally grouped for bounded joins."""

    def __init__(self, rows: Iterable[Any] = ()) -> None:
        self._db = sqlite3.connect("", check_same_thread=False)
        self._db.execute("PRAGMA cache_size=-256")
        self._db.execute("PRAGMA temp_store=FILE")
        self._db.execute("PRAGMA journal_mode=DELETE")
        self._db.execute("CREATE TABLE rows(seq INTEGER PRIMARY KEY, group_key TEXT, payload TEXT NOT NULL)")
        self._db.execute("CREATE INDEX group_rows ON rows(group_key,seq)")
        self.extend(rows)

    @staticmethod
    def _decode(raw):
        import pickle
        return pickle.loads(raw)

    def append(self, row: Any, *, group: str | None = None) -> None:
        import pickle
        encoded = pickle.dumps(row, protocol=5)
        self._db.execute("INSERT INTO rows(group_key,payload) VALUES(?,?)", (group, encoded))

    def extend(self, rows: Iterable[Any]) -> None:
        for row in rows:
            self.append(row)

    def __iter__(self):
        for row in self._db.execute("SELECT payload FROM rows ORDER BY seq"):
            yield self._decode(row[0])

    def __reversed__(self):
        for row in self._db.execute("SELECT payload FROM rows ORDER BY seq DESC"):
            yield self._decode(row[0])

    def group(self, key: str):
        for row in self._db.execute("SELECT payload FROM rows WHERE group_key=? ORDER BY seq", (key,)):
            yield self._decode(row[0])

    def __len__(self):
        return int(self._db.execute("SELECT coalesce(max(seq),0) FROM rows").fetchone()[0])

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            return DiskRows(self[i] for i in range(start, stop, step))
        if index < 0:
            index += len(self)
        row = self._db.execute("SELECT payload FROM rows WHERE seq=?", (index+1,)).fetchone()
        if row is None:
            raise IndexError(index)
        return self._decode(row[0])

    def sort(self, *, key=None, reverse=False):
        # Keys are produced and consumed only inside this temporary database;
        # pickle preserves Python tuple/datetime ordering without coercion.
        import pickle
        from functools import lru_cache
        key = key or (lambda row: row)
        self._db.create_function("row_sort_key", 1, lambda raw: pickle.dumps(key(self._decode(raw)), protocol=5).hex())
        @lru_cache(maxsize=256)
        def decode(raw):
            return pickle.loads(bytes.fromhex(raw))
        def compare(a, b):
            left, right = decode(a), decode(b)
            return (left > right) - (left < right)
        self._db.create_collation("python_key", compare)
        self._db.execute("SAVEPOINT disk_rows_sort")
        try:
            self._db.execute("CREATE TEMP TABLE sorted_input AS SELECT seq,group_key,payload,row_sort_key(payload) AS sort_key FROM rows")
            self._db.execute("CREATE TABLE sorted_output(seq INTEGER PRIMARY KEY, group_key TEXT, payload TEXT NOT NULL)")
            direction = "DESC" if reverse else "ASC"
            self._db.execute("INSERT INTO sorted_output(group_key,payload) SELECT group_key,payload FROM sorted_input ORDER BY sort_key COLLATE python_key " + direction + ",seq ASC")
            self._db.execute("DROP TABLE rows")
            self._db.execute("ALTER TABLE sorted_output RENAME TO rows")
            self._db.execute("CREATE INDEX group_rows ON rows(group_key,seq)")
        except BaseException:
            self._db.execute("ROLLBACK TO disk_rows_sort")
            raise
        finally:
            self._db.execute("DROP TABLE IF EXISTS sorted_input")
            self._db.execute("DROP TABLE IF EXISTS sorted_output")
            self._db.execute("RELEASE disk_rows_sort")
            self._db.create_function("row_sort_key", 1, None)
            self._db.create_collation("python_key", None)
            decode.cache_clear()

    def __eq__(self, other):
        from itertools import zip_longest
        if not hasattr(other, '__iter__'):
            return False
        sentinel = object()
        return all(a == b for a, b in zip_longest(self, other, fillvalue=sentinel))

    def close(self):
        self._db.close()

    def __del__(self):
        if hasattr(self, '_db'):
            self._db.close()


def write_json_value(handle, value: Any, *, ensure_ascii: bool = False) -> None:
    """Stream derived views without constructing their entire JSON string."""
    from collections.abc import Mapping
    if isinstance(value, Mapping):
        handle.write('{')
        first = True
        pairs = value.items()
        if isinstance(value, DiskMap):
            pairs = ((key, value._decode(payload)) for key, payload in value._db.execute('SELECT key,value FROM items ORDER BY key'))
        else:
            pairs = ((key, value[key]) for key in sorted(value))
        for key, item in pairs:
            if not first:
                handle.write(',')
            first = False
            handle.write(json.dumps(str(key), ensure_ascii=ensure_ascii))
            handle.write(':')
            write_json_value(handle, item, ensure_ascii=ensure_ascii)
        handle.write('}')
    elif isinstance(value, (DiskRows, list, tuple)):
        handle.write('[')
        for index, item in enumerate(value):
            if index:
                handle.write(',')
            write_json_value(handle, item, ensure_ascii=ensure_ascii)
        handle.write(']')
    else:
        handle.write(json.dumps(value, ensure_ascii=ensure_ascii, allow_nan=False, default=str))


def text_lines(path, *, encoding='utf-8', errors=None):
    """A list-like, disk-backed replacement for whole-file splitlines."""
    with path.open(encoding=encoding, errors=errors) as handle:
        # Preserve line payloads; universal-newline handling matches text IO.
        return DiskRows(line.rstrip('\r\n') for line in handle)


def disk_sorted(rows, *, key=None, reverse=False):
    output = DiskRows(rows)
    output.sort(key=key, reverse=reverse)
    return output


def canonical_json_hash(value):
    import hashlib
    digest = hashlib.sha256()
    class Sink:
        def write(self, text):
            digest.update(text.encode())
    write_json_value(Sink(), value, ensure_ascii=True)
    return digest.hexdigest()


def read_json_state(path, *, array_fields=(), map_fields=(), disk_object=False):
    """Read a JSON object while spooling selected historical arrays.

    Wire shape is unchanged. Only one array element plus a 64 KiB input chunk
    is decoded at a time; malformed input still raises JSONDecodeError.
    """
    decoder = json.JSONDecoder()
    with path.open(encoding='utf-8') as handle:
        buffer = ''
        eof = False
        def refill():
            nonlocal buffer, eof
            chunk = handle.read(65536)
            eof = not chunk
            buffer += chunk
        def whitespace():
            nonlocal buffer
            while True:
                buffer = buffer.lstrip()
                if buffer or eof:
                    return
                refill()
        def expect(char):
            nonlocal buffer
            whitespace()
            if not buffer.startswith(char):
                raise json.JSONDecodeError(f'expected {char}', buffer, 0)
            buffer = buffer[1:]
        def value():
            nonlocal buffer
            whitespace()
            while True:
                try:
                    item, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    if eof:
                        raise
                    refill()
                    continue
                if end == len(buffer) and not eof:
                    refill()
                    continue
                if end < len(buffer) and buffer[end] not in ',:]} \r\n\t':
                    if not eof and not any(c in ',:]} \r\n\t' for c in buffer[end:]):
                        refill()
                        continue
                    raise json.JSONDecodeError('invalid value suffix', buffer, end)
                buffer = buffer[end:]
                return item
        expect('{')
        result = DiskMap() if disk_object else {}
        whitespace()
        if buffer.startswith('}'):
            buffer = buffer[1:]
        else:
            while True:
                key = value()
                if not isinstance(key, str):
                    raise json.JSONDecodeError('object key must be a string', buffer, 0)
                expect(':')
                whitespace()
                if key in array_fields and buffer.startswith('['):
                    expect('[')
                    rows = DiskRows()
                    whitespace()
                    if buffer.startswith(']'):
                        buffer = buffer[1:]
                    else:
                        while True:
                            rows.append(value())
                            whitespace()
                            if buffer.startswith(']'):
                                buffer = buffer[1:]
                                break
                            expect(',')
                    result[key] = rows
                elif key in map_fields and buffer.startswith('{'):
                    expect('{')
                    rows = DiskMap()
                    whitespace()
                    if buffer.startswith('}'):
                        buffer = buffer[1:]
                    else:
                        while True:
                            item_key = value()
                            if not isinstance(item_key, str):
                                raise json.JSONDecodeError('object key must be a string', buffer, 0)
                            expect(':')
                            rows[item_key] = value()
                            whitespace()
                            if buffer.startswith('}'):
                                buffer = buffer[1:]
                                break
                            expect(',')
                    result[key] = rows
                else:
                    result[key] = value()
                whitespace()
                if buffer.startswith('}'):
                    buffer = buffer[1:]
                    break
                expect(',')
        whitespace()
        if buffer:
            raise json.JSONDecodeError('extra data', buffer, 0)
        return result


class _GroupRows:
    def __init__(self, rows, key):
        self.rows, self.key = rows, key

    def append(self, value):
        self.rows.append(value, group=self.key)

    def __iter__(self):
        return self.rows.group(self.key)

    def __len__(self):
        return self.rows._db.execute('SELECT count(*) FROM rows WHERE group_key=?', (self.key,)).fetchone()[0]

    def __getitem__(self, index):
        if isinstance(index, slice):
            from itertools import islice
            start, stop, step = index.indices(len(self))
            return DiskRows(islice(self, start, stop, step))
        if index < 0:
            index += len(self)
        if index < 0:
            raise IndexError(index)
        row = self.rows._db.execute('SELECT payload FROM rows WHERE group_key=? ORDER BY seq LIMIT 1 OFFSET ?', (self.key, index)).fetchone()
        if row is None:
            raise IndexError(index)
        return self.rows._decode(row[0])


class DiskGroups:
    """One disk spool for grouped rows, avoiding a cache/connection per group.

    Group keys are local strings or tuples of strings; values are repeatable
    views. Mutations must use append on a view, as with defaultdict(list).
    """
    def __init__(self):
        self.rows = DiskRows()
        self.keys = DiskMap()

    @staticmethod
    def _key(key):
        return json.dumps(key, ensure_ascii=True, separators=(',', ':'))

    def __getitem__(self, key):
        encoded = self._key(key)
        self.keys[encoded] = key
        return _GroupRows(self.rows, encoded)

    def __iter__(self):
        return iter(self.keys.values())

    def __len__(self):
        return len(self.keys)

    def get(self, key, default=None):
        return self[key] if self._key(key) in self.keys else default

    def setdefault(self, key, default):
        if self._key(key) not in self.keys:
            group = self[key]
            for value in default:
                group.append(value)
            return group
        return self[key]

    def items(self):
        for key in self:
            yield key, self.get(key)

    def ordered_items(self):
        for key in disk_sorted(self):
            yield key, self.get(key)
