import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from peach_price.normalize import aggregate_day
from history_prices import history_week

class Store:
    def __init__(self, docs): self.docs=docs
    def get_year(self, year): return [x for x in self.docs if x['date'].startswith(str(year))]

def raw(grade, kg, total, amount):
    return dict(gds_mclsf_nm='복숭아', gds_sclsf_nm='백도', unit_qty=str(kg), unit_nm='kg',
                unit_tot_qty=str(total), totprc=str(amount), avgprc=str(amount/(total/kg)),
                grd_nm=grade, whsl_mrkt_cd='110001')

def test_grade_aggregates():
    a=aggregate_day([raw('특',4,40,160000),raw('상',4,80,240000)])
    assert a['sizes']['4']['totprc']==400000
    assert a['grades']['4']['특']['won_per_kg']==4000
    assert a['grades']['4']['상']['won_per_kg']==3000
    d={'date':'2026-06-15','year':2026,'md':'06-15',**a}
    whole=history_week(Store([d]),2026,6,3,'4')
    assert whole['average_box_price']==round(400000/120*4)
    assert whole['available_grades']==['상','특']
    assert whole['grade_data_ready']
    only=history_week(Store([d]),2026,6,3,'4','특')
    assert only['average_box_price']==16000
    assert only['trading_days']==1
    assert only['days'][1]['box_price'] is None

def test_old_data_not_misrepresented():
    d={'date':'2026-06-15','year':2026,'md':'06-15','sizes':{'4':{'total_kg':100,'totprc':300000}}}
    out=history_week(Store([d]),2026,6,3,'4','특')
    assert not out['grade_data_ready']
    assert out['average_box_price'] is None

if __name__=='__main__':
    test_grade_aggregates();test_old_data_not_misrepresented();print('2 tests passed')
