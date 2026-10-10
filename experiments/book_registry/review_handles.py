"""Persistent human-readable references; never renumber records after append/review."""
import re
import threading

TYPES = {'entity': ('entities', 'entity_id', 'E'),
         'fact': ('facts', 'fact_id', 'F'),
         'mention': ('mentions', 'mention_id', 'M')}


def paragraph_handle(pid):
    match = re.fullmatch(r'SU-(\d+)-P(\d+)', pid or '')
    return f'P{int(match[1])}-{int(match[2])}' if match else pid


class ReviewHandles:
    def __init__(self, registry):
        self.reg = registry
        self.lock = threading.Lock()
        self.signature = None
        with self.reg.connection(write=True) as con:
            con.execute('''CREATE TABLE IF NOT EXISTS review_handles(
                handle TEXT PRIMARY KEY, record_type TEXT NOT NULL,
                record_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                UNIQUE(record_type,record_id), UNIQUE(record_type,ordinal))''')
        self.sync()

    def sync(self):
        with self.lock:
            with self.reg.connection() as con:
                signature = tuple(con.execute(f'SELECT coalesce(max(rowid),0) FROM {table}').fetchone()[0]
                                  for table, _, _ in TYPES.values())
            if signature == self.signature:
                return
            # Number allocation is serialized with extraction commits; only this index is written.
            with self.reg.connection(write=True) as con:
                for kind, (table, key, prefix) in TYPES.items():
                    start = con.execute('SELECT coalesce(max(ordinal),0) FROM review_handles WHERE record_type=?', (kind,)).fetchone()[0]
                    missing = con.execute(f'''SELECT t.{key} FROM {table} t WHERE NOT EXISTS
                        (SELECT 1 FROM review_handles h WHERE h.record_type=? AND h.record_id=t.{key})
                        ORDER BY t.rowid''', (kind,)).fetchall()
                    con.executemany('INSERT INTO review_handles VALUES (?,?,?,?)',
                        ((f'{prefix}{start+i:06d}', kind, r[0], start+i) for i,r in enumerate(missing, 1)))
            self.signature = signature

    def decorate(self, value):
        self.sync()
        records = []
        def collect(obj):
            if isinstance(obj, dict):
                records.append(obj)
                for field,child in list(obj.items()):
                    if field not in ('value','value_json','origin_json'): collect(child)
            elif isinstance(obj, list):
                for child in obj: collect(child)
        collect(value)
        mappings = {}
        with self.reg.connection() as con:
            for kind, (_, key, _) in TYPES.items():
                fields = [key] + (['subject_id','object_id','speaker_id'] if kind=='entity' else [])
                ids = {obj[field] for obj in records for field in fields if isinstance(obj.get(field),str)}
                found = {}
                ordered = sorted(ids)
                for offset in range(0,len(ordered),400):
                    group = ordered[offset:offset+400]
                    found.update(con.execute('SELECT record_id,handle FROM review_handles WHERE record_type=? AND record_id IN ('+','.join('?' for _ in group)+')',[kind]+group))
                mappings[kind] = found
        for obj in records:
            for kind in ('fact','mention','entity'):
                key = TYPES[kind][1]
                if 'handle' not in obj and obj.get(key) in mappings[kind]:
                    obj['handle'] = mappings[kind][obj[key]]
                    break
            for field in ('subject','object','speaker'):
                if obj.get(field+'_id') in mappings['entity']:
                    obj[field+'_handle'] = mappings['entity'][obj[field+'_id']]
            if obj.get('paragraph_id'):
                obj['paragraph_handle'] = paragraph_handle(obj['paragraph_id'])
        return value

    def resolve(self, code):
        code = code.strip().upper()
        short = re.fullmatch(r'([EFM])(\d+)',code)
        if short: code = f'{short[1]}{int(short[2]):06d}'
        with self.reg.connection() as con:
            if match := re.fullmatch(r'P(\d+)-(\d+)',code):
                pid = f'SU-{int(match[1]):06d}-P{int(match[2]):04d}'
                row = con.execute('SELECT paragraph_id,unit_id FROM paragraphs WHERE paragraph_id=?',(pid,)).fetchone()
                if row: return {'record_type':'paragraph','record_id':pid,'handle':paragraph_handle(pid),**dict(row)}
            row = con.execute('SELECT handle,record_type,record_id FROM review_handles WHERE handle=?',(code,)).fetchone()
            if row:
                result = dict(row)
                if result['record_type']=='fact':
                    result['entity_id'] = con.execute('SELECT subject_id FROM facts WHERE fact_id=?',(result['record_id'],)).fetchone()[0]
                elif result['record_type']=='mention':
                    result.update(con.execute('''SELECT m.entity_id,e.paragraph_id FROM mentions m JOIN evidence e USING(evidence_id)
                        WHERE m.mention_id=?''',(result['record_id'],)).fetchone())
                return result
        raise ValueError('没有找到这个编号。请使用 E、F、M 编号或 P源单元-段落号。')
