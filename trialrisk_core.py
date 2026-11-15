"""TrialRisk core: shared by the notebooks AND the web app, so a live trial gets exactly the same features."""
import re
import duckdb
import numpy as np
import pandas as pd

STOPWORDS = r'terminat|withdr|stopped early|discontinu|halted|prematurely'
AREAS = ['Neoplasms', 'Cardiovascular Diseases', 'Nervous System Diseases', 'Mental Disorders', 'Infections',
         'Respiratory Tract Diseases', 'Nutritional and Metabolic Diseases', 'Digestive System Diseases',
         'Musculoskeletal Diseases', 'Immune System Diseases', 'Skin and Connective Tissue Diseases', 'Eye Diseases']
ITYPES = ['DRUG', 'BIOLOGICAL', 'DEVICE', 'PROCEDURE', 'BEHAVIORAL', 'RADIATION', 'DIETARY_SUPPLEMENT',
          'GENETIC', 'COMBINATION_PRODUCT', 'DIAGNOSTIC_TEST', 'OTHER']
CATEGORICAL = ['phase', 'allocation', 'intervention_model', 'primary_purpose', 'masking', 'sponsor_class', 'sex']
ELIG_FLAGS = {
    'elig_pregnancy': r'pregnan|lactat|breast-?feed',
    'elig_performance_status': r'ecog|karnofsky|performance status',
    'elig_prior_therapy': r'prior (?:therapy|treatment|chemo|systemic)|previously treated|refractory|relapsed',
    'elig_biopsy': r'biops|histolog|patholog',
    'elig_lab_limits': r'creatinine|bilirubin|platelet|neutrophil|hemoglobin|\buln\b',
    'elig_contraception': r'contracept',
    'elig_measurable_disease': r'measurable disease|recist',
    'elig_brain_metastases': r'brain metast|cns metast',
    'elig_genetic_marker': r'mutation|her2|egfr|alk|braf|kras|biomarker|expression',
}
TITLE_FLAGS = {
    'title_pilot': r'pilot|feasib',
    'title_randomized': r'randomi[sz]',
    'title_placebo': r'placebo',
    'title_open_label': r'open[- ]label',
    'title_extension': r'extension|long[- ]term|follow[- ]up',
    'title_comparison': r' versus | vs\.? |compar',
    'title_first_in_human': r'first[- ]in[- ]human|dose[- ]escalation|maximum tolerated',
}
SUSPICIOUS = ['n_locations', 'n_countries', 'has_us_site', 'months_registered_before_start']
NOT_FEATURES = ['start_year']  # used for the time split only: test years are outside the training range
EMBED_MODEL = 'BAAI/bge-small-en-v1.5'


def dig(d, path, default=None):
    for k in path.split('.'):
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def flatten(study):
    """One trial's ClinicalTrials.gov JSON -> one flat row (same as Phase 1)."""
    p = study.get('protocolSection', {})
    idm, st, sp, des = p.get('identificationModule', {}), p.get('statusModule', {}), p.get('sponsorCollaboratorsModule', {}), p.get('designModule', {})
    arms, out, el, ov = p.get('armsInterventionsModule', {}), p.get('outcomesModule', {}), p.get('eligibilityModule', {}), p.get('oversightModule', {})
    dinfo = des.get('designInfo') or {}
    interventions = arms.get('interventions') or []
    locations = dig(p, 'contactsLocationsModule.locations') or []
    countries = sorted({l.get('country') for l in locations if l.get('country')})
    joined = lambda xs: '|'.join(str(x) for x in xs if x)
    return {
        'nct_id': idm.get('nctId'), 'brief_title': idm.get('briefTitle'), 'official_title': idm.get('officialTitle'),
        'status': st.get('overallStatus'), 'why_stopped': st.get('whyStopped'),
        'start_date': dig(st, 'startDateStruct.date'), 'start_date_type': dig(st, 'startDateStruct.type'),
        'first_submit_date': st.get('studyFirstSubmitDate'),
        'primary_completion_date': dig(st, 'primaryCompletionDateStruct.date'),
        'completion_date': dig(st, 'completionDateStruct.date'), 'last_update_date': st.get('lastUpdateSubmitDate'),
        'sponsor_name': dig(sp, 'leadSponsor.name'), 'sponsor_class': dig(sp, 'leadSponsor.class'),
        'n_collaborators': len(sp.get('collaborators') or []),
        'has_dmc': ov.get('oversightHasDmc'), 'fda_drug': ov.get('isFdaRegulatedDrug'), 'fda_device': ov.get('isFdaRegulatedDevice'),
        'brief_summary': dig(p, 'descriptionModule.briefSummary'),
        'conditions': joined(dig(p, 'conditionsModule.conditions') or []),
        'keywords': joined(dig(p, 'conditionsModule.keywords') or []),
        'study_type': des.get('studyType'), 'phases': joined(des.get('phases') or []),
        'allocation': dinfo.get('allocation'), 'intervention_model': dinfo.get('interventionModel'),
        'primary_purpose': dinfo.get('primaryPurpose'),
        'masking': dig(dinfo, 'maskingInfo.masking') or dig(des, 'maskingInfo.masking'),
        'enrollment': dig(des, 'enrollmentInfo.count'), 'enrollment_type': dig(des, 'enrollmentInfo.type'),
        'n_arms': len(arms.get('armGroups') or []), 'n_interventions': len(interventions),
        'intervention_types': joined(sorted({i.get('type') for i in interventions if i.get('type')})),
        'intervention_names': joined(i.get('name') for i in interventions)[:2000],
        'n_primary_outcomes': len(out.get('primaryOutcomes') or []), 'n_secondary_outcomes': len(out.get('secondaryOutcomes') or []),
        'eligibility': el.get('eligibilityCriteria'), 'sex': el.get('sex'), 'min_age': el.get('minimumAge'),
        'max_age': el.get('maximumAge'), 'healthy_volunteers': el.get('healthyVolunteers'), 'std_ages': joined(el.get('stdAges') or []),
        'n_locations': len(locations), 'n_countries': len(countries), 'countries': joined(countries),
        'mesh_terms': joined(m.get('term') for m in dig(study, 'derivedSection.conditionBrowseModule.meshes') or []),
        'mesh_ancestors': joined(m.get('term') for m in dig(study, 'derivedSection.conditionBrowseModule.ancestors') or []),
        'has_results': study.get('hasResults'),
    }


def month_index(s):
    """'2015-03' or '2015-03-12' -> months since year 0 (NaN if missing). A bare year counts as January."""
    if not isinstance(s, str) or len(s) < 4:
        return np.nan
    try:
        return int(s[:4]) * 12 + (int(s[5:7]) - 1 if len(s) >= 7 else 0)
    except ValueError:
        return np.nan


def age_years(s):
    """'18 Years', '6 Months', '2 Weeks', 'N/A' -> years."""
    if not isinstance(s, str):
        return np.nan
    m = re.match(r'\s*([\d.]+)\s*(year|month|week|day|hour|minute)', s.lower())
    if not m:
        return np.nan
    per = {'year': 1, 'month': 12, 'week': 52.18, 'day': 365.25, 'hour': 8766, 'minute': 525960}[m.group(2)]
    return float(m.group(1)) / per


def sponsor_key(name):
    s = re.sub(r'[^a-z0-9 ]', ' ', str(name or '').lower())
    s = re.sub(r'\b(inc|ltd|llc|corp|corporation|co|company|gmbh|ag|sa|plc|limited|the)\b', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _count(pattern, s):
    return len(re.findall(pattern, s)) if isinstance(s, str) else 0


def design_features(df):
    """Design-time features for one or many trials (a DataFrame of flattened rows)."""
    f = pd.DataFrame(index=df.index)
    f['phase'] = df.phases.fillna('').replace('', 'NONE')
    for c in ['allocation', 'intervention_model', 'primary_purpose', 'masking', 'sponsor_class', 'sex']:
        f[c] = df[c].fillna('NONE')
    for c in ['has_dmc', 'fda_drug', 'fda_device', 'healthy_volunteers']:
        f[c] = df[c].map({True: 1.0, False: 0.0})
    for c in ['n_arms', 'n_interventions', 'n_primary_outcomes', 'n_secondary_outcomes', 'n_collaborators']:
        f[c] = pd.to_numeric(df[c], errors='coerce')
    types = df.intervention_types.fillna('')
    for t in ITYPES:
        f[f'type_{t.lower()}'] = types.str.contains(t, regex=False).astype(float)
    f['n_conditions'] = df.conditions.fillna('').map(lambda s: len([x for x in s.split('|') if x]))
    f['n_keywords'] = df.keywords.fillna('').map(lambda s: len([x for x in s.split('|') if x]))
    mesh = (df.mesh_ancestors.fillna('') + '|' + df.mesh_terms.fillna('')).str.lower()
    for a in AREAS:
        f['area_' + re.sub(r'[^a-z]+', '_', a.lower()).strip('_')] = mesh.str.contains(a.lower(), regex=False).astype(float)
    f['min_age_years'] = df.min_age.map(age_years)
    f['max_age_years'] = df.max_age.map(age_years)
    f['age_span_years'] = f.max_age_years - f.min_age_years
    ages = df.std_ages.fillna('')
    f['includes_children'] = ages.str.contains('CHILD').astype(float)
    f['includes_older_adults'] = ages.str.contains('OLDER_ADULT').astype(float)
    el = df.eligibility.fillna('').str.lower()
    parts = el.str.split('exclusion criteria', n=1, regex=False)
    inc, exc = parts.str[0], parts.str[1].fillna('')
    bullet = r'(?m)^\s*(?:[*\-•]|\d+[.)])\s+'
    f['elig_length'] = el.str.len()
    f['n_inclusion_rules'] = inc.map(lambda s: _count(bullet, s))
    f['n_exclusion_rules'] = exc.map(lambda s: _count(bullet, s))
    f['n_numbers_in_rules'] = el.map(lambda s: _count(r'\d+(?:\.\d+)?', s))
    for k, rx in ELIG_FLAGS.items():
        f[k] = el.str.contains(rx, regex=True).astype(float)
    title = (df.brief_title.fillna('') + ' ' + df.official_title.fillna('')).str.lower()
    for k, rx in TITLE_FLAGS.items():
        f[k] = title.str.contains(rx, regex=True).astype(float)
    f['title_length'] = df.official_title.fillna(df.brief_title).fillna('').str.len()
    f['summary_length'] = df.brief_summary.fillna('').str.len()
    f['start_year'] = df.start_date.map(month_index) // 12
    # suspicious fields: kept, but tested with and without in Phase 5
    f['n_locations'] = pd.to_numeric(df.n_locations, errors='coerce')
    f['n_countries'] = pd.to_numeric(df.n_countries, errors='coerce')
    f['has_us_site'] = df.countries.fillna('').str.contains('United States', regex=False).astype(float)
    f['months_registered_before_start'] = df.start_date.map(month_index) - df.first_submit_date.map(month_index)
    return f


def history_table(trials):
    """Every finished trial's sponsor, start month, end month and outcome (for sponsor track records)."""
    h = pd.DataFrame({
        'sponsor': trials.sponsor_name.map(sponsor_key),
        'start_m': trials.start_date.map(month_index),
        'end_m': trials.completion_date.fillna(trials.primary_completion_date).fillna(trials.last_update_date).map(month_index),
        'stopped': trials.status.isin(['TERMINATED', 'WITHDRAWN']).astype(int),
    })
    h['end_m'] = np.fmax(h.end_m, h.start_m)  # an end date before the start is a typo: treat it as the start
    return h[(h.sponsor != '') & h.start_m.notna()].reset_index(drop=True)


def sponsor_features(df, hist, prior_rate=0.15, strength=10):
    """Sponsor track record known on the trial's start date: trials started before, and how many of the ones that
    had ALREADY ENDED were stopped. Only the past is used, so nothing leaks."""
    q = pd.DataFrame({'row': np.arange(len(df)), 'sponsor': df.sponsor_name.map(sponsor_key).to_numpy(),
                      'start_m': df.start_date.map(month_index).astype(float).to_numpy()})
    con = duckdb.connect()
    con.register('q', q)
    con.register('h', hist)
    out = con.sql(f"""
      WITH starts AS (SELECT sponsor, start_m, count(*) AS n FROM h GROUP BY 1, 2),
           c1 AS (SELECT sponsor, start_m, sum(n) OVER (PARTITION BY sponsor ORDER BY start_m) AS prior_trials FROM starts),
           ends AS (SELECT sponsor, end_m, count(*) AS n, sum(stopped) AS s FROM h WHERE end_m IS NOT NULL GROUP BY 1, 2),
           c2 AS (SELECT sponsor, end_m, sum(n) OVER (PARTITION BY sponsor ORDER BY end_m) AS prior_ended,
                         sum(s) OVER (PARTITION BY sponsor ORDER BY end_m) AS prior_stopped FROM ends),
           a AS (SELECT q.row, c1.prior_trials FROM q ASOF LEFT JOIN c1 ON q.sponsor = c1.sponsor AND q.start_m > c1.start_m),
           b AS (SELECT q.row, c2.prior_ended, c2.prior_stopped FROM q ASOF LEFT JOIN c2 ON q.sponsor = c2.sponsor AND q.start_m > c2.end_m)
      SELECT a.row, coalesce(a.prior_trials, 0) AS sponsor_prior_trials, coalesce(b.prior_ended, 0) AS sponsor_prior_ended,
             (coalesce(b.prior_stopped, 0) + {prior_rate * strength}) / (coalesce(b.prior_ended, 0) + {strength}) AS sponsor_prior_stop_rate
      FROM a JOIN b USING (row) ORDER BY a.row""").df()
    con.close()
    out.index = df.index
    return out.drop(columns='row')


def trial_text(df):
    """The words the AI embedding model reads for each trial (stopping words removed, kept short for speed)."""
    txt = lambda v: v if isinstance(v, str) else ''
    clean = lambda s, n: re.sub(STOPWORDS, ' ', txt(s), flags=re.I)[:n]
    return [f"{clean(txt(r.official_title) or r.brief_title, 250)}. Conditions: {clean(txt(r.conditions).replace('|', ', '), 150)}. "
            f"Treatments: {clean(txt(r.intervention_names).replace('|', ', '), 150)}. Phase: {txt(r.phases) or 'none'}. "
            f"{clean(r.brief_summary, 350)} Eligibility: {clean(r.eligibility, 300)}"
            for r in df.itertuples()]


NAMES = {
    'phase': 'Phase', 'allocation': 'Randomized or not', 'intervention_model': 'Study design', 'primary_purpose': 'Main purpose',
    'masking': 'Blinding', 'sponsor_class': 'Sponsor type', 'sex': 'Sex allowed', 'has_dmc': 'Has a data monitoring committee',
    'fda_drug': 'FDA-regulated drug', 'fda_device': 'FDA-regulated device', 'healthy_volunteers': 'Accepts healthy volunteers',
    'n_arms': 'Number of study arms', 'n_interventions': 'Number of treatments', 'n_primary_outcomes': 'Number of primary outcomes',
    'n_secondary_outcomes': 'Number of secondary outcomes', 'n_collaborators': 'Number of collaborators',
    'n_conditions': 'Number of conditions', 'n_keywords': 'Number of keywords', 'min_age_years': 'Minimum age (years)',
    'max_age_years': 'Maximum age (years)', 'age_span_years': 'Age range (years)', 'includes_children': 'Includes children',
    'includes_older_adults': 'Includes adults 65+', 'elig_length': 'Length of eligibility rules',
    'n_inclusion_rules': 'Number of inclusion rules', 'n_exclusion_rules': 'Number of exclusion rules',
    'n_numbers_in_rules': 'Numbers in the eligibility rules', 'title_length': 'Title length', 'summary_length': 'Summary length',
    'start_year': 'Start year', 'sponsor_prior_trials': "Sponsor's earlier trials", 'sponsor_prior_ended': "Sponsor's earlier finished trials",
    'sponsor_prior_stop_rate': "Sponsor's past stop rate", 'n_locations': 'Number of study sites', 'n_countries': 'Number of countries',
    'has_us_site': 'Has a US site', 'months_registered_before_start': 'Months registered before start',
}


def label(col):
    """Plain-English name of a feature, for charts, explanations and the website."""
    if col in NAMES:
        return NAMES[col]
    for pre, text in [('type_', 'Treatment type: '), ('area_', 'Disease area: '), ('elig_', 'Rules mention '),
                      ('title_', 'Title says '), ('text_', 'Text pattern ')]:
        if col.startswith(pre):
            return text + col[len(pre):].replace('_', ' ')
    return col.replace('_', ' ').capitalize()


def load_embedder():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=EMBED_MODEL)


def embed(texts, model, batch_size=64):
    v = np.array(list(model.embed(list(texts), batch_size=batch_size)), dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True).clip(1e-9)
# ===== END OF TRIALRISK CORE =====
