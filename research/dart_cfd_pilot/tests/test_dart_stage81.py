import importlib.util, json, subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RUNNER=ROOT/'scripts'/'run_dart_stage81_recall_expansion.py'

def load():
    s=importlib.util.spec_from_file_location('s81',RUNNER);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def test_nearest_level_a_is_bounded_and_deterministic():
    m=load();rows=[{'x_physical':'1.0','y_physical':'2.0','reference_id':'P1'},{'x_physical':'1.1','y_physical':'2.0','reference_id':'P2'}]
    assert m.nearest_level_a({'x_physical':1.02,'y_physical':2.0},rows,.06)[1]=='P1'
    assert m.nearest_level_a({'x_physical':1.07,'y_physical':2.0},rows,.02) is None

def test_config_uses_predeclared_sensitivity_point():
    c=json.loads((ROOT/'dart_stage81.json').read_text()); assert c['level_b_quantile']==.980;assert c['level_b_gamma2_threshold']==.70;assert c['minimum_level_b_observations']>=3

def test_submit_reuses_raw_fields_and_stage8():
    compile(RUNNER.read_text(),str(RUNNER),'exec');p=ROOT/'scripts'/'submit_unity_dart_stage81.sh';r=subprocess.run(['bash','-n',str(p)],capture_output=True,text=True);assert r.returncode==0,r.stderr
    t=p.read_text();assert 'RUN_OK_RAW_FIELDS.txt' in t;assert 'stage8_catalogue.csv' in t;assert 'mfc.sh run' not in t
