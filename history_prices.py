"""월별 1~7, 8~14, 15~21, 22~28, 29~말일 기준 주차별 과거 시세."""
from calendar import monthrange
from datetime import date, timedelta



def history_week(store, year: int, month: int, week: int, size: str = '4') -> dict:
    start_day = (week - 1) * 7 + 1
    last_day = monthrange(year, month)[1]
    if start_day > last_day:
        raise ValueError('해당 월에 존재하지 않는 주차입니다')
    start = date(year, month, start_day)
    end = date(year, month, min(start_day + 6, last_day))
    docs = {d['date']: d for d in store.get_year(year)}
    points = []
    total_won = 0.0
    total_kg = 0.0
    day = start
    while day <= end:
        doc = docs.get(day.isoformat()) or {}
        row = (doc.get('sizes') or {}).get(size) or {}
        kg = float(row.get('total_kg') or 0)
        won = float(row.get('totprc') or 0)
        avg = round(won / kg * float(size)) if kg > 0 and won > 0 else None
        if avg is not None:
            total_kg += kg
            total_won += won
        points.append({'date': day.isoformat(), 'box_price': avg,
                       'won_per_kg': round(won / kg) if avg is not None else None,
                       'total_kg': kg if avg is not None else None})
        day += timedelta(days=1)
    vals = [p['box_price'] for p in points if p['box_price'] is not None]
    change = round((vals[-1] - vals[0]) / vals[0] * 100, 1) if len(vals) >= 2 and vals[0] else None
    return {'year': year, 'month': month, 'week': week, 'size_kg': size,
            'start_date': start.isoformat(), 'end_date': end.isoformat(),
            'basis': '같은 시기 조생 백도계 복숭아 도매 시세 (미홍 단일 품종 가격 아님)',
            'days': points, 'trading_days': len(vals),
            'average_box_price': round(total_won / total_kg * float(size)) if total_kg else None,
            'min_box_price': min(vals) if vals else None,
            'max_box_price': max(vals) if vals else None,
            'change_pct': change}
