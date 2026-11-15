"""TrialRisk API: predicts whether a clinical trial will stop early, explains why, and finds similar past trials."""
import json
import os
import re
import time
from typing import Literal, Optional
import pandas as pd
import requests
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
import trialrisk_core as core
from trialrisk_engine import LRU, Engine, blank_trial, lever_values, pretty

HERE = os.path.dirname(os.path.abspath(__file__))
CTGOV = os.environ.get('CTGOV_API', 'https://clinicaltrials.gov/api/v2/studies')
FINISHED = {'COMPLETED', 'TERMINATED', 'WITHDRAWN'}
app = FastAPI(title='TrialRisk API', version='1.0',
              description='Will this clinical trial stop early? Scores any ClinicalTrials.gov trial, explains the score, '
                          'and shows similar past trials. Research demo, not advice for real trial decisions.')
engine = Engine(HERE)
load = lambda name: json.load(open(f'{HERE}/data/{name}'))
SUMMARY, SHOWCASE = load('summary.json'), load('showcase.json')
studies = LRU(300)
session = requests.Session()
session.headers['User-Agent'] = 'TrialRisk portfolio demo'


def fetch_study(nct_id):
    """One trial's full record from ClinicalTrials.gov."""
    try:
        r = session.get(f'{CTGOV}/{nct_id}', timeout=30)
    except requests.RequestException:
        raise HTTPException(503, 'ClinicalTrials.gov did not answer. Please try again in a minute.')
    if r.status_code == 404:
        raise HTTPException(404, f'{nct_id} was not found on ClinicalTrials.gov.')
    if r.status_code != 200:
        raise HTTPException(503, f'ClinicalTrials.gov answered {r.status_code}. Please try again in a minute.')
    return r.json()


def fetch_search(params):
    try:
        r = session.get(CTGOV, params=params, timeout=30)
        r.raise_for_status()
        return r.json().get('studies', [])
    except requests.RequestException:
        raise HTTPException(503, 'ClinicalTrials.gov did not answer. Please try again in a minute.')


def trial_row(nct_id):
    nct = nct_id.strip().upper()
    if not re.fullmatch(r'NCT\d{8}', nct):
        raise HTTPException(422, 'A trial ID looks like NCT followed by 8 digits, for example NCT04368728.')
    study = studies.get(nct)
    if study is None:
        study = fetch_study(nct)
        studies.put(nct, study)
    row = core.flatten(study)
    if row['study_type'] != 'INTERVENTIONAL':
        raise HTTPException(422, f"{nct} is an {str(row['study_type']).lower()} study. TrialRisk scores interventional trials only.")
    return row


def levers(phase: Optional[Literal['EARLY_PHASE1', 'PHASE1', 'PHASE1|PHASE2', 'PHASE2', 'PHASE2|PHASE3', 'PHASE3', 'PHASE4', 'NA']] = None,
           allocation: Optional[Literal['RANDOMIZED', 'NON_RANDOMIZED', 'NA']] = None,
           masking: Optional[Literal['NONE', 'SINGLE', 'DOUBLE', 'TRIPLE', 'QUADRUPLE']] = None,
           purpose: Optional[Literal['TREATMENT', 'PREVENTION', 'SUPPORTIVE_CARE', 'DIAGNOSTIC', 'BASIC_SCIENCE',
                                     'HEALTH_SERVICES_RESEARCH', 'SCREENING', 'OTHER']] = None,
           sponsor: Optional[str] = Query(None, max_length=120, description="Lead sponsor's name, e.g. Pfizer"),
           sponsor_type: Optional[Literal['INDUSTRY', 'OTHER', 'NIH', 'OTHER_GOV', 'NETWORK', 'FED', 'INDIV']] = None,
           arms: Optional[int] = Query(None, ge=1, le=20), primary_outcomes: Optional[int] = Query(None, ge=0, le=30),
           secondary_outcomes: Optional[int] = Query(None, ge=0, le=80), inclusion_rules: Optional[int] = Query(None, ge=0, le=60),
           exclusion_rules: Optional[int] = Query(None, ge=0, le=80), collaborators: Optional[int] = Query(None, ge=0, le=30),
           healthy_volunteers: Optional[bool] = None, dmc: Optional[bool] = None, fda_drug: Optional[bool] = None):
    args = dict(locals())
    return {k: v for k, v in args.items() if v is not None}


@app.get('/api/health')
def health():
    return {'status': 'ok', 'model': engine.b['version'], 'similar_trials_indexed': 0 if engine.vec is None else len(engine.vec)}


@app.get('/api/summary')
def summary():
    """Model results, data facts and what drives the score (for the website's overview)."""
    return SUMMARY


@app.get('/api/showcase')
def showcase():
    """Real 2018-2019 trials scored by the model, next to what really happened."""
    return SHOWCASE


@app.get('/api/trial/{nct_id}')
def trial(nct_id: str):
    """Fetch any trial from ClinicalTrials.gov and score it: risk, reasons and similar past trials."""
    t0 = time.time()
    row = trial_row(nct_id)
    res = engine.score_rows(pd.DataFrame([row]))[0]
    res['finished'] = row['status'] in FINISHED
    res['why_stopped'] = row.get('why_stopped')
    if res['finished']:
        res['note'] = 'This trial has already ended, so you can compare the prediction with what really happened.'
    res['levers'] = lever_values(row)
    res['seconds'] = round(time.time() - t0, 2)
    return res


@app.get('/api/search')
def search(q: str = Query(..., min_length=2, max_length=80, description='A disease or treatment, e.g. breast cancer'),
           status: Literal['RECRUITING', 'NOT_YET_RECRUITING', 'ACTIVE_NOT_RECRUITING'] = 'RECRUITING',
           limit: int = Query(10, ge=1, le=20)):
    """Find live interventional trials on ClinicalTrials.gov. Pick one and score it with /api/trial/{nct_id}."""
    found = fetch_search({'query.cond': q, 'filter.overallStatus': status, 'pageSize': 40})
    out = []
    for s in found:
        row = core.flatten(s)
        if row['study_type'] != 'INTERVENTIONAL' or not row['nct_id']:
            continue
        studies.put(row['nct_id'], s)
        out.append({'nct_id': row['nct_id'], 'title': row['brief_title'], 'phase': pretty('phase', row['phases'] or 'NONE'),
                    'sponsor': row['sponsor_name'], 'conditions': (row['conditions'] or '').replace('|', ', ')[:120],
                    'start': row['start_date'], 'status': row['status'], 'sites': row['n_locations']})
        if len(out) >= limit:
            break
    return {'query': q, 'status': status, 'count': len(out), 'trials': out}


@app.get('/api/whatif')
def whatif(nct_id: str, changes: dict = Depends(levers)):
    """Change a trial's design (phase, blinding, rules, sponsor...) and see how the risk moves."""
    return engine.what_if(trial_row(nct_id), changes)


@app.get('/api/design')
def design(title: str = Query('A trial of my new drug', max_length=200), condition: str = Query('Type 2 diabetes', max_length=120),
           treatment: str = Query('New drug', max_length=120), summary: str = Query('', max_length=600),
           changes: dict = Depends(levers)):
    """Design a brand-new trial from scratch and score it."""
    row = blank_trial(title, condition, treatment, changes.get('sponsor') or 'My Company', summary)
    result = engine.what_if(row, changes, k=20)
    result['after'].update(nct_id='MY-TRIAL')
    return result


STATIC = f'{HERE}/static'
if os.path.exists(f'{STATIC}/index.html'):
    app.mount('/', StaticFiles(directory=STATIC, html=True), name='site')
