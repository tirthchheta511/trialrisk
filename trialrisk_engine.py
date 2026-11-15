"""TrialRisk engine: risk score, plain-English reasons, similar-trial search and what-if redesign.
Used by the notebooks AND the website, so both always give the same answer. Built to fit a small free server:
the search vectors stay on disk (memory-mapped) and the tables live in DuckDB, not in pandas."""
import datetime as dt
import hashlib
import json
import os
import re
import threading
from collections import OrderedDict
import duckdb
import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import trialrisk_core as core

PHASES = {'NA': 'No phase (device, behavioral, etc.)', 'NONE': 'Not given', 'EARLY_PHASE1': 'Early phase 1'}
FLAG_PREFIXES = ('type_', 'area_', 'elig_', 'title_', 'includes_', 'has_', 'fda_', 'healthy_')
NEUTRAL_RULES = ['Able to give written informed consent', 'Willing to attend all study visits',
                 'Lives within travel distance of a study site', 'Not taking part in another interventional study',
                 'Agrees to follow the study procedures', 'No condition that would make taking part unsafe',
                 'Able to read and understand the consent form', 'Has a working phone for study contact']
AREA_WORDS = {'Neoplasms': r'cancer|tumou?r|carcinoma|lymphoma|leukemia|leukaemia|melanoma|sarcoma|myeloma|glioma|neoplasm',
              'Cardiovascular Diseases': r'heart|cardiac|hypertension|stroke|atrial|coronary|vascular',
              'Nervous System Diseases': r'alzheimer|parkinson|epilep|multiple sclerosis|migraine|neuropath|dementia',
              'Mental Disorders': r'depress|anxiety|schizophren|bipolar|autism|adhd|substance|alcohol|opioid|insomnia',
              'Infections': r'hiv|covid|infection|hepatitis|tuberculosis|influenza|malaria|sepsis',
              'Respiratory Tract Diseases': r'asthma|copd|lung|pneumonia|respiratory|cystic fibrosis',
              'Nutritional and Metabolic Diseases': r'diabetes|obesity|metabolic|lipid|cholesterol',
              'Musculoskeletal Diseases': r'arthritis|osteo|back pain|muscle|bone',
              'Skin and Connective Tissue Diseases': r'psoriasis|dermatitis|eczema|skin|lupus',
              'Digestive System Diseases': r'crohn|colitis|liver|bowel|gastr|pancrea'}
LEVERS = {  # name on the website -> (column in the flattened trial, kind)
    'phase': ('phases', 'text'), 'allocation': ('allocation', 'text'), 'masking': ('masking', 'text'),
    'purpose': ('primary_purpose', 'text'), 'sponsor': ('sponsor_name', 'text'), 'sponsor_type': ('sponsor_class', 'text'),
    'arms': ('n_arms', 'int'), 'primary_outcomes': ('n_primary_outcomes', 'int'), 'secondary_outcomes': ('n_secondary_outcomes', 'int'),
    'collaborators': ('n_collaborators', 'int'), 'healthy_volunteers': ('healthy_volunteers', 'bool'), 'dmc': ('has_dmc', 'bool'),
    'fda_drug': ('fda_drug', 'bool'), 'inclusion_rules': (None, 'rules'), 'exclusion_rules': (None, 'rules'),
}
BULLET = r'(?m)^\s*(?:[*\-•]|\d+[.)])\s+(.+?)\s*$'


def label(col):
    return 'AI reading of the protocol text' if col == 'ai_text_score' else core.label(col)


def pretty(col, v, contribution=0.0):
    """A feature value in words, e.g. 'Phase 2', '32%', 'yes'."""
    if col == 'ai_text_score':
        return 'reads like trials that stopped' if contribution > 0 else 'reads like trials that finished'
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 'not given'
    if col in ('phase', 'phases'):
        s = str(v) or 'NONE'
        return PHASES.get(s, re.sub(r'PHASE(\d)', r'Phase \1', s).replace('|', ' + '))
    if isinstance(v, str):
        if col == 'masking' and v == 'NONE':
            return 'None (open label)'
        return {'NONE': 'not given', 'NA': 'not applicable'}.get(v, v.replace('_', ' ').lower().capitalize())
    if col.startswith(FLAG_PREFIXES) and float(v) in (0.0, 1.0):
        return 'yes' if float(v) == 1.0 else 'no'
    if col == 'sponsor_prior_stop_rate':
        return f'{100 * v:.0f}%'
    if col.endswith('_years'):
        return f'{12 * v:.0f} months' if 0 < v < 1 else f'{v:.0f} years'
    return f'{v:,.0f}' if float(v).is_integer() else f'{v:,.1f}'


def split_rules(text):
    """Eligibility text -> (inclusion rules, exclusion rules) as lists of lines."""
    parts = re.split(r'(?i)exclusion criteria:?', text if isinstance(text, str) else '', maxsplit=1)
    return re.findall(BULLET, parts[0]), (re.findall(BULLET, parts[1]) if len(parts) > 1 else [])


def build_eligibility(inc, exc):
    return 'Inclusion Criteria:\n\n' + '\n'.join(f'* {r}' for r in inc) + '\n\nExclusion Criteria:\n\n' + '\n'.join(f'* {r}' for r in exc)


def resize(rules, n):
    return rules[:n] + [NEUTRAL_RULES[i % len(NEUTRAL_RULES)] for i in range(max(0, n - len(rules)))]


def apply_changes(row, changes):
    """Copy of one flattened trial with the website's what-if levers applied. Returns (new row, list of changes)."""
    row = dict(row)
    done = []
    inc, exc = split_rules(row.get('eligibility'))
    for name, value in changes.items():
        if value is None or name not in LEVERS:
            continue
        col, kind = LEVERS[name]
        if kind == 'rules':
            old = len(inc) if name == 'inclusion_rules' else len(exc)
            if int(value) != old:
                inc, exc = (resize(inc, int(value)), exc) if name == 'inclusion_rules' else (inc, resize(exc, int(value)))
                row['eligibility'] = build_eligibility(inc, exc)
                done.append({'lever': name, 'from': old, 'to': int(value)})
            continue
        new = int(value) if kind == 'int' else bool(value) if kind == 'bool' else str(value)
        if row.get(col) != new:
            done.append({'lever': name, 'from': row.get(col), 'to': new})
            row[col] = new
    return row, done


def lever_values(row):
    inc, exc = split_rules(row.get('eligibility'))
    out = {name: row.get(col) for name, (col, kind) in LEVERS.items() if col}
    out.update(inclusion_rules=len(inc), exclusion_rules=len(exc))
    clean = lambda v: None if v is None or (isinstance(v, float) and v != v) else (v.item() if hasattr(v, 'item') else v)
    return {k: clean(v) for k, v in out.items()}


def blank_trial(title, condition, treatment, sponsor, summary=''):
    """A new trial for 'design your own': sensible defaults the user then changes with the levers."""
    today = dt.date.today().strftime('%Y-%m')
    areas = [a for a, rx in AREA_WORDS.items() if re.search(rx, f'{condition} {title}'.lower())]
    return {'nct_id': 'MY-TRIAL', 'brief_title': title, 'official_title': title, 'status': 'NOT_YET_RECRUITING', 'why_stopped': None,
            'start_date': today, 'first_submit_date': today, 'sponsor_name': sponsor, 'sponsor_class': 'INDUSTRY', 'n_collaborators': 0,
            'has_dmc': True, 'fda_drug': True, 'fda_device': False,
            'brief_summary': summary or f'This study tests {treatment} in people with {condition}.',
            'conditions': condition, 'keywords': '', 'study_type': 'INTERVENTIONAL', 'phases': 'PHASE2', 'allocation': 'RANDOMIZED',
            'intervention_model': 'PARALLEL', 'primary_purpose': 'TREATMENT', 'masking': 'DOUBLE', 'n_arms': 2, 'n_interventions': 2,
            'intervention_types': 'DRUG', 'intervention_names': f'{treatment}|Placebo', 'n_primary_outcomes': 1, 'n_secondary_outcomes': 5,
            'eligibility': build_eligibility(resize([], 8), resize([], 12)), 'sex': 'ALL', 'min_age': '18 Years', 'max_age': None,
            'healthy_volunteers': False, 'std_ages': 'ADULT|OLDER_ADULT', 'n_locations': 0, 'n_countries': 0, 'countries': '',
            'mesh_terms': condition, 'mesh_ancestors': '|'.join(areas)}


class LRU(OrderedDict):
    def __init__(self, size):
        super().__init__()
        self.size = size

    def get(self, key):
        if key in self:
            self.move_to_end(key)
            return self[key]
        return None

    def put(self, key, value):
        self[key] = value
        self.move_to_end(key)
        while len(self) > self.size:
            self.popitem(last=False)


def load_server_embedder(app_dir):
    """The AI text model, set up to use little memory: one thread, no memory arena, and the int8 version
    when Phase 8 chose it (data/embedder.json). TRIALRISK_EMBEDDER=fp32|int8 overrides the choice."""
    from fastembed import TextEmbedding
    cfg_path = f'{app_dir}/data/embedder.json'
    variant = json.load(open(cfg_path)).get('variant', 'fp32') if os.path.exists(cfg_path) else 'fp32'
    variant = os.environ.get('TRIALRISK_EMBEDDER', variant)
    kw = dict(threads=1, enable_cpu_mem_arena=False)
    if variant == 'int8' and os.path.isdir(f'{app_dir}/model/embedder_int8'):
        kw['specific_model_path'] = f'{app_dir}/model/embedder_int8'
    return TextEmbedding(model_name=core.EMBED_MODEL, **kw)


class Engine:
    def __init__(self, app_dir):
        self.app_dir = app_dir
        self.b = joblib.load(f'{app_dir}/model/trialrisk_model.joblib')
        self.booster = lgb.Booster(model_str=self.b['booster'])
        self.cols = self.b['columns']
        self.base_rate = self.b['base_rate']
        self.con = duckdb.connect()
        self.con.execute("SET threads = 2; SET memory_limit = '200MB'")
        self.con.execute(f"CREATE TABLE hist AS SELECT * FROM read_parquet('{app_dir}/data/sponsor_history.parquet')")
        self.vec = None
        if os.path.exists(f'{app_dir}/data/similar_vectors.npy'):
            self.vec = np.load(f'{app_dir}/data/similar_vectors.npy', mmap_mode='r')
            z = np.load(f'{app_dir}/data/similar_pca.npz')
            self.pmean, self.pcomp = z['pca_mean'], z['pca_components']
            # trial details stay on disk; only the start years are kept in memory (for the 'earlier trials only' filter)
            self.con.execute(f"CREATE VIEW meta AS SELECT * FROM read_parquet('{app_dir}/data/similar_trials.parquet')")
            m = self.con.execute('SELECT start_year FROM meta ORDER BY idx').df()
            self.years = m.start_year.fillna(0).to_numpy(np.int16)
        self.embedder, self.lock, self.cache = None, threading.Lock(), LRU(512)

    # ---------- AI text ----------
    def embed_rows(self, rows):
        """384 numbers per trial from its protocol text (cached, one at a time on a small server)."""
        texts = core.trial_text(rows)
        keys = [hashlib.md5(t.encode()).hexdigest() for t in texts]
        todo = [i for i, k in enumerate(keys) if self.cache.get(k) is None]
        if todo:
            with self.lock:
                if self.embedder is None:
                    self.embedder = load_server_embedder(self.app_dir)
                new = core.embed([texts[i] for i in todo], self.embedder)
            for i, v in zip(todo, new):
                self.cache.put(keys[i], v.astype(np.float32))
        return np.vstack([self.cache.get(k) for k in keys])

    # ---------- model ----------
    def history(self, rows):
        cur = self.con.cursor()
        cur.register('keys_df', pd.DataFrame({'sponsor': sorted(set(rows.sponsor_name.map(core.sponsor_key)))}))
        h = cur.execute('SELECT * FROM hist WHERE sponsor IN (SELECT sponsor FROM keys_df)').df()
        cur.close()
        return h

    def features(self, rows, emb):
        """Flattened registry rows + their embeddings -> the model's input table."""
        rows = rows.reset_index(drop=True).copy()
        today = dt.date.today().strftime('%Y-%m')
        rows['start_date'] = [s if isinstance(s, str) and s else today for s in rows.start_date]
        X = pd.concat([core.design_features(rows), core.sponsor_features(rows, self.history(rows))], axis=1)
        emb = np.asarray(emb, dtype=np.float32).reshape(len(rows), -1)
        if self.b['uses_text_score']:
            X['ai_text_score'] = emb @ self.b['text_coef'] + self.b['text_intercept']
        if self.b['uses_text_patterns']:
            X[[f'text_{i}' for i in range(self.b['pca_components'].shape[0])]] = (emb - self.b['pca_mean']) @ self.b['pca_components'].T
        for c in self.b['categorical']:
            X[c] = pd.Categorical(X[c].astype(str), categories=self.b['categories'][c])
        return X[self.cols]

    def predict(self, X):
        raw = self.booster.predict(X)
        return raw, np.interp(raw, self.b['iso_x'], self.b['iso_y'])

    def contributions(self, X):
        """SHAP values (built into LightGBM): how much each feature pushed this trial's risk up or down."""
        return self.booster.predict(X, pred_contrib=True)[:, :-1]

    def grouped(self, C):
        """Merge all AI text columns into one item, 'AI reading of the protocol text'."""
        text = [j for j, c in enumerate(self.cols) if c.startswith('text_') or c == 'ai_text_score']
        keep = [j for j in range(len(self.cols)) if j not in text]
        names = [self.cols[j] for j in keep] + (['ai_text_score'] if text else [])
        return names, np.column_stack([C[:, keep]] + ([C[:, text].sum(1)] if text else []))

    def band(self, risk):
        return 'High' if risk >= self.b['bands']['high'] else 'Medium' if risk >= self.b['bands']['medium'] else 'Low'

    def reasons(self, X, C, i, n=4):
        names, G = self.grouped(C)
        items = [{'feature': c, 'label': label(c), 'value': pretty(c, X.iloc[i][c] if c in X else None, G[i, j]),
                  'effect': round(float(G[i, j]), 4)} for j, c in enumerate(names)]
        up = sorted([r for r in items if r['effect'] > 0], key=lambda r: -r['effect'])[:n]
        down = sorted([r for r in items if r['effect'] < 0], key=lambda r: r['effect'])[:n]
        return up, down

    # ---------- similar trials ----------
    def similar(self, emb, k=20, before_year=None, exclude=None):
        """The k most similar finished trials (cosine similarity of their protocol text)."""
        q = (np.asarray(emb, dtype=np.float32).reshape(-1) - self.pmean) @ self.pcomp.T
        q /= max(np.linalg.norm(q), 1e-9)
        scale = 1 / 127 if self.vec.dtype == np.int8 else 1.0      # int8 vectors were stored as round(vector * 127)
        sims = np.concatenate([np.asarray(self.vec[s:s + 20000], dtype=np.float32) @ q for s in range(0, len(self.vec), 20000)]) * scale
        if before_year is not None:
            sims[self.years >= before_year] = -2
        cur = self.con.cursor()
        if exclude:
            hit = cur.execute('SELECT idx FROM meta WHERE nct_id = ?', [exclude]).fetchone()
            if hit:
                sims[hit[0]] = -2
        top = np.argpartition(-sims, k)[:k]
        top = top[np.argsort(-sims[top])]
        out = cur.execute(f"SELECT * FROM meta WHERE idx IN ({','.join(str(int(i)) for i in top)})").df()
        cur.close()
        out = out.set_index('idx').loc[top].reset_index(drop=True)
        out['similarity'] = sims[top].round(3)
        return out

    # ---------- what the website shows ----------
    def report(self, rows, emb, k=20, before_years=None):
        """Risk, band, reasons and similar past trials for each row."""
        rows = rows.reset_index(drop=True)
        emb = np.asarray(emb, dtype=np.float32).reshape(len(rows), -1)
        X = self.features(rows, emb)
        raw, risk = self.predict(X)
        C = self.contributions(X)
        txt = lambda v: v if isinstance(v, str) else ''
        names, G = self.grouped(C)
        out = []
        for i, r in rows.iterrows():
            up, down = self.reasons(X, C, i)
            res = {'nct_id': r.nct_id, 'title': txt(r.brief_title), 'status': txt(r.status), 'phase': pretty('phase', X.iloc[i]['phase']),
                   'sponsor': txt(r.sponsor_name), 'conditions': txt(r.conditions).replace('|', ', '),
                   'start': txt(r.start_date), 'risk': round(float(risk[i]), 4), 'model_score': round(float(raw[i]), 6), 'band': self.band(risk[i]),
                   'average_risk': round(self.base_rate, 4), 'times_average': round(float(risk[i]) / self.base_rate, 2),
                   'raises_risk': up, 'lowers_risk': down,
                   'facts': {c: pretty(c, X.iloc[i][c]) for c in names if c in X and c != 'ai_text_score'},
                   'effects': {c: round(float(G[i, j]), 3) for j, c in enumerate(names)}}
            if self.vec is not None and k:
                by = None if before_years is None else before_years[i]
                sim = self.similar(emb[i], k, before_year=by, exclude=r.nct_id)
                stopped = sim[sim.status != 'COMPLETED']
                top = sim.head(6)[['nct_id', 'title', 'status', 'why_stopped', 'start_year', 'similarity']].astype(object)
                res['similar'] = top.where(top.notna(), None).to_dict('records')
                res['similar_checked'] = int(len(sim))
                res['similar_stopped'] = int(len(stopped))
                res['similar_reasons'] = {key: int(v) for key, v in stopped.why_group.fillna('Not given').value_counts().items()}
            out.append(res)
        return out

    def score_rows(self, rows, k=20):
        return self.report(rows, self.embed_rows(rows), k=k)

    def score_study(self, study, embedder=None, k=20):
        """One trial straight from the ClinicalTrials.gov API -> full report."""
        if embedder is not None:
            self.embedder = embedder
        return self.score_rows(pd.DataFrame([core.flatten(study)]), k=k)[0]

    def what_if(self, row, changes, k=0):
        """Same trial with some design choices changed. The AI's reading of the text is kept, so only the levers move."""
        base = pd.DataFrame([row])
        emb = self.embed_rows(base)
        new_row, done = apply_changes(row, changes)
        before = self.report(base, emb, k=0)[0]
        after = self.report(pd.DataFrame([new_row]), emb, k=k)[0]
        return {'before': {'risk': before['risk'], 'band': before['band']}, 'after': after, 'changes': done,
                'levers': lever_values(new_row)}
# ===== END OF TRIALRISK ENGINE =====
