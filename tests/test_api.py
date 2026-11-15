"""Automated checks for the TrialRisk API. ClinicalTrials.gov is replaced by a saved trial, so the tests run offline."""
import json
import os
import sys
import pytest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fastapi import HTTPException
from fastapi.testclient import TestClient
import main

SAMPLE = json.load(open(f'{HERE}/sample_study.json'))
NCT = SAMPLE['protocolSection']['identificationModule']['nctId']
client = TestClient(main.app)


def fake_fetch(nct_id):
    if nct_id != NCT:
        raise HTTPException(404, f'{nct_id} was not found on ClinicalTrials.gov.')
    return SAMPLE


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(main, 'fetch_study', fake_fetch)
    monkeypatch.setattr(main, 'fetch_search', lambda params: [SAMPLE])
    main.studies.clear()


def test_health():
    r = client.get('/api/health')
    assert r.status_code == 200 and r.json()['status'] == 'ok'


def test_summary_and_showcase():
    s = client.get('/api/summary').json()
    assert 0.5 < s['metrics']['test_auroc'] <= 1
    show = client.get('/api/showcase').json()
    assert len(show) >= 3 and all(0 <= t['risk'] <= 1 for t in show)


def test_trial_is_scored_and_explained():
    d = client.get(f'/api/trial/{NCT}').json()
    assert 0 <= d['risk'] <= 1 and d['band'] in ('Low', 'Medium', 'High')
    assert d['raises_risk'] or d['lowers_risk']
    assert len(d['similar']) > 0 and d['similar_checked'] == 20
    assert d['levers']['exclusion_rules'] >= 0


def test_bad_and_unknown_ids():
    assert client.get('/api/trial/hello').status_code == 422
    assert client.get('/api/trial/NCT00000000').status_code == 404


def test_search_lists_trials():
    d = client.get('/api/search', params={'q': 'cancer'}).json()
    assert d['count'] == 1 and d['trials'][0]['nct_id'] == NCT


def test_whatif_without_changes_keeps_the_score():
    d = client.get('/api/whatif', params={'nct_id': NCT}).json()
    assert d['changes'] == [] and abs(d['before']['risk'] - d['after']['risk']) < 1e-9


def test_whatif_changes_move_the_score():
    base = client.get(f'/api/trial/{NCT}').json()
    d = client.get('/api/whatif', params={'nct_id': NCT, 'exclusion_rules': 70, 'sponsor': 'A brand new sponsor',
                                          'masking': 'NONE', 'phase': 'PHASE1|PHASE2'}).json()
    assert d['levers']['exclusion_rules'] == 70 and len(d['changes']) >= 3
    assert d['after']['model_score'] != base['model_score']


def test_design_your_own_trial():
    d = client.get('/api/design', params={'title': 'Pilot study of drug X in breast cancer', 'condition': 'Breast cancer',
                                          'exclusion_rules': 30}).json()
    assert d['after']['nct_id'] == 'MY-TRIAL' and 0 <= d['after']['risk'] <= 1 and len(d['after']['similar']) > 0


def test_bad_lever_values_are_rejected():
    assert client.get('/api/whatif', params={'nct_id': NCT, 'arms': 999}).status_code == 422
    assert client.get('/api/whatif', params={'nct_id': NCT, 'phase': 'PHASE9'}).status_code == 422
